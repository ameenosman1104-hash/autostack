"""Stock Intelligence Engine - Deterministic business metrics for inventory analysis.

This service calculates actionable stock metrics based on actual historical data.
All calculations are deterministic, multi-tenant isolated, and suitable for
later AI explanation layers.

NO external AI is used. This is pure backend calculation.

MOVEMENT CLASSIFICATION ALGORITHM:
- Relative to tenant's own sales distribution (not fixed universal thresholds)
- Fast-moving: top 25% of velocity among products with sales
- Normal-moving: middle 50%
- Slow-moving: bottom 25% OR < 0.5 units/month
- Dormant: no sales in 90+ days (separate from slow-moving)
- Insufficient data: < 30 days old OR no sales history to compare
"""

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
from decimal import Decimal, ROUND_HALF_UP


class StockIntelligenceEngine:
    """Deterministic stock analysis engine with tenant-relative classification."""

    def __init__(self, tenant_id: str, get_conn_func):
        """Initialize with tenant context.

        Args:
            tenant_id: Multi-tenant isolation key
            get_conn_func: Callable that returns db connection: get_conn(tenant_id)
        """
        self.tenant_id = tenant_id
        self.get_conn = get_conn_func

    def analyze_inventory(self) -> Dict[str, Any]:
        """
        Comprehensive inventory analysis.

        Returns structured data with summary and detailed attention items.
        """
        conn = self.get_conn(self.tenant_id)
        try:
            # Single query: get all products with sales data
            products_data = self._get_products_with_sales_data(conn)

            # Calculate velocity distribution for tenant-relative classification
            velocity_distribution = self._calculate_velocity_distribution(products_data)

            # Calculate metrics for each product
            attention_items = []
            for product_data in products_data:
                metrics = self._calculate_product_metrics(product_data, velocity_distribution)
                if metrics['flags']:  # Only include products needing attention
                    attention_items.append(metrics)

            # Sort by urgency
            attention_items.sort(key=lambda x: self._urgency_score(x), reverse=True)

            # Generate summary
            summary = self._generate_summary(products_data, attention_items)

            return {
                'summary': summary,
                'attention_items': attention_items,
                'generated_at': datetime.now().isoformat()
            }
        finally:
            conn.close()

    def _get_products_with_sales_data(self, conn) -> List[Dict]:
        """
        Fetch all products with aggregated sales data in single query.

        Only counts COMPLETED invoices. Draft/cancelled/failed invoices ignored.
        Avoids N+1 problem by joining with sales aggregations.
        """
        query = """
            SELECT
                p.id,
                p.code,
                p.name,
                p.category,
                p.brand,
                p.tyre_size,
                p.condition,
                p.current_stock,
                p.reorder_level,
                p.created_at,
                p.updated_at,

                -- Sales aggregations from invoices (ONLY completed status)
                COALESCE(SUM(CASE
                    WHEN i.sale_date >= date('now', '-7 days') AND i.status = 'completed'
                    THEN ii.quantity
                END), 0) as qty_sold_7d,

                COALESCE(SUM(CASE
                    WHEN i.sale_date >= date('now', '-30 days') AND i.status = 'completed'
                    THEN ii.quantity
                END), 0) as qty_sold_30d,

                COALESCE(SUM(CASE
                    WHEN i.sale_date >= date('now', '-90 days') AND i.status = 'completed'
                    THEN ii.quantity
                END), 0) as qty_sold_90d,

                MAX(CASE
                    WHEN ii.product_id IS NOT NULL AND i.status = 'completed'
                    THEN i.sale_date
                END) as last_sold_date,

                COUNT(DISTINCT CASE
                    WHEN ii.product_id IS NOT NULL AND i.status = 'completed'
                    THEN i.id
                END) as sale_count

            FROM products p
            LEFT JOIN invoice_items ii ON ii.product_id = p.id
            LEFT JOIN invoices i ON i.id = ii.invoice_id

            WHERE (p.deleted = 0 OR p.deleted IS NULL)
            GROUP BY p.id
            ORDER BY p.name
        """

        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    def _calculate_velocity_distribution(self, products_data: List[Dict]) -> Dict[str, Any]:
        """
        Calculate this tenant's sales velocity distribution for relative classification.

        Algorithm:
        1. Collect 30-day velocities from all products with sales
        2. Calculate percentiles (p25, p50, p75)
        3. Handle edge cases (very few products, no sales, etc.)

        Returns dict with percentile thresholds for classification.
        """
        # Collect velocities from products with sales
        velocities = []
        for product in products_data:
            qty_30d = float(product['qty_sold_30d'])
            if qty_30d > 0:  # Only consider products with actual sales
                avg_daily = qty_30d / 30.0
                velocities.append(avg_daily)

        # Handle edge cases
        if not velocities:
            # No products with sales - return defaults for classification
            return {
                'has_sales_data': False,
                'product_count_with_sales': 0,
                'p25': 0.0,
                'p50': 0.0,
                'p75': 0.0,
                'classification_method': 'no_data'
            }

        # Sort for percentile calculation
        velocities.sort()
        n = len(velocities)

        # Calculate percentiles (simple method: index-based)
        p25_idx = max(0, int(n * 0.25) - 1)
        p50_idx = max(0, int(n * 0.50) - 1)
        p75_idx = max(0, int(n * 0.75) - 1)

        return {
            'has_sales_data': True,
            'product_count_with_sales': n,
            'p25': velocities[p25_idx],
            'p50': velocities[p50_idx],
            'p75': velocities[p75_idx],
            'classification_method': 'relative_percentile'
        }

    def _calculate_product_metrics(self, product_data: Dict,
                                   velocity_dist: Dict) -> Dict[str, Any]:
        """Calculate all metrics for a single product."""

        product_id = product_data['id']
        current_stock = float(product_data['current_stock'])
        reorder_level = float(product_data['reorder_level'])

        qty_7d = float(product_data['qty_sold_7d'])
        qty_30d = float(product_data['qty_sold_30d'])
        qty_90d = float(product_data['qty_sold_90d'])

        last_sold_date = product_data['last_sold_date']
        sale_count = int(product_data['sale_count'])

        # Calculate derived metrics
        avg_daily_30d = qty_30d / 30.0 if qty_30d > 0 else 0
        avg_daily_90d = qty_90d / 90.0 if qty_90d > 0 else 0

        days_since_last_sale = self._calculate_days_since(last_sold_date)

        stock_cover_30d = self._calculate_stock_cover(
            current_stock, avg_daily_30d, 30
        )
        stock_cover_90d = self._calculate_stock_cover(
            current_stock, avg_daily_90d, 90
        )

        # Determine flags (deterministic rules, no AI)
        flags = self._determine_flags(
            product_data, current_stock, reorder_level,
            qty_7d, qty_30d, qty_90d,
            days_since_last_sale, avg_daily_30d, stock_cover_30d,
            sale_count
        )

        classification_fast = self._classify_fast_moving(
            avg_daily_30d, velocity_dist, product_data['created_at']
        )
        classification_slow = self._classify_slow_moving(
            avg_daily_30d, qty_30d, days_since_last_sale, sale_count,
            velocity_dist, product_data['created_at']
        )

        return {
            'product_id': product_id,
            'product_code': product_data['code'],
            'product_name': product_data['name'],
            'category': product_data.get('category', ''),
            'brand': product_data.get('brand', ''),
            'tyre_size': product_data.get('tyre_size', ''),
            'condition': product_data.get('condition', ''),

            # Current state
            'current_stock': current_stock,
            'reorder_level': reorder_level,
            'stock_vs_reorder': current_stock - reorder_level,
            'is_reorder_level_set': reorder_level > 0,

            # Sales metrics (ONLY from completed invoices)
            'units_sold_7d': qty_7d,
            'units_sold_30d': qty_30d,
            'units_sold_90d': qty_90d,
            'average_daily_sales_30d': round(avg_daily_30d, 4),
            'average_daily_sales_90d': round(avg_daily_90d, 4),
            'total_sales_transactions': sale_count,

            # Last sold
            'last_sold_date': last_sold_date,
            'days_since_last_sale': days_since_last_sale,
            'has_ever_sold': sale_count > 0,

            # Stock cover (days until stockout at current velocity)
            'estimated_days_remaining_30d': stock_cover_30d,
            'estimated_days_remaining_90d': stock_cover_90d,
            'stock_cover_reliability_30d': 'high' if qty_30d > 0 else 'insufficient',
            'stock_cover_reliability_90d': 'high' if qty_90d > 0 else 'insufficient',

            # Classifications and flags
            'fast_moving_status': classification_fast['status'],
            'fast_moving_reason': classification_fast['reason'],
            'slow_moving_status': classification_slow['status'],
            'slow_moving_reason': classification_slow['reason'],
            'dormant_status': 'dormant' if (days_since_last_sale and days_since_last_sale > 90) else 'active',
            'flags': flags
        }

    def _determine_flags(self, product: Dict, current_stock: float,
                         reorder_level: float, qty_7d: float, qty_30d: float,
                         qty_90d: float, days_since_last: Optional[int],
                         avg_daily_30d: float, stock_cover_30d: Optional[float],
                         sale_count: int) -> List[str]:
        """
        Determine which action flags apply to this product.

        Flags are deterministic rules based on business state.
        Only COMPLETED invoices contribute (verified in _get_products_with_sales_data).
        """
        flags = []

        # OUT_OF_STOCK: Impossible to fulfill orders
        if current_stock <= 0:
            flags.append('OUT_OF_STOCK')

        # BELOW_REORDER_LEVEL: Match existing AutoStack logic (<=)
        # Verified against get_low_stock_products() implementation
        elif reorder_level > 0 and current_stock <= reorder_level:
            flags.append('BELOW_REORDER_LEVEL')

        # LOW_STOCK_FAST_MOVING: Critical combination
        # Stock is low AND product sells regularly
        if (current_stock < reorder_level * 2 and qty_30d > 0 and
            avg_daily_30d >= 0.5):  # Moving at least 0.5 units/day
            flags.append('LOW_STOCK_FAST_MOVING')

        # STOCK_COVER_LOW: At current velocity, will run out soon
        # Use 30-day average (more recent) if available, else 90-day
        relevant_cover = stock_cover_30d if stock_cover_30d else None
        if relevant_cover is not None and relevant_cover < 7:
            flags.append('STOCK_COVER_LOW')

        # SLOW_MOVING_STOCK: Product has stock but sells rarely
        # Don't flag newly-created products (< 30 days old)
        if (current_stock > reorder_level * 2 and
            qty_30d < 1 and
            not self._is_newly_created(product['created_at'])):
            flags.append('SLOW_MOVING_STOCK')

        # NEVER_SOLD: Product has never been sold
        # Don't flag if product created less than 30 days ago
        if sale_count == 0 and not self._is_newly_created(product['created_at']):
            flags.append('NEVER_SOLD')

        # NO_REORDER_LEVEL: Product exists but reorder threshold not set
        # Only flag if product has been created for > 30 days
        if reorder_level <= 0 and not self._is_newly_created(product['created_at']):
            flags.append('NO_REORDER_LEVEL')

        # DORMANT: No sales in 90+ days (separate from SLOW_MOVING_STOCK)
        if days_since_last is not None and days_since_last > 90 and sale_count > 0:
            flags.append('DORMANT')

        return flags

    def _classify_fast_moving(self, avg_daily: float, velocity_dist: Dict,
                              created_at: str) -> Dict[str, str]:
        """
        Classify as fast-moving relative to this tenant's distribution.

        Algorithm:
        - Insufficient data: product < 30 days old
        - Dormant: no sales in recent history
        - Relative classification: compare to tenant's percentiles
        """
        if self._is_newly_created(created_at):
            return {
                'status': 'insufficient_data',
                'reason': 'Product created < 30 days ago'
            }

        if avg_daily == 0:
            return {
                'status': 'never_sold',
                'reason': 'No sales history'
            }

        if not velocity_dist['has_sales_data']:
            # Tenant has no sales data at all
            return {
                'status': 'insufficient_data',
                'reason': 'Tenant has no sales history yet'
            }

        # Classify relative to tenant's distribution
        if avg_daily >= velocity_dist['p75']:
            return {
                'status': 'fast_moving',
                'reason': f'Top 25% in this shop ({avg_daily:.2f} units/day)'
            }

        if avg_daily >= velocity_dist['p50']:
            return {
                'status': 'normal_moving',
                'reason': f'Top 50% in this shop ({avg_daily:.2f} units/day)'
            }

        if avg_daily >= velocity_dist['p25']:
            return {
                'status': 'slow_moving',
                'reason': f'Bottom 50% in this shop ({avg_daily:.2f} units/day)'
            }

        return {
            'status': 'very_slow_moving',
            'reason': f'Bottom 25% in this shop ({avg_daily:.2f} units/day)'
        }

    def _classify_slow_moving(self, avg_daily: float, qty_30d: float,
                              days_since_last: Optional[int], sale_count: int,
                              velocity_dist: Dict, created_at: str) -> Dict[str, str]:
        """
        Classify as slow-moving with dormant as separate status.

        Algorithm:
        - Insufficient data: product < 30 days old
        - Dormant: 90+ days without a sale (separate concept)
        - Never sold: sale_count = 0
        - Slow-moving: < 0.5 units/month AND not dormant
        - Not slow-moving: otherwise
        """
        if self._is_newly_created(created_at):
            return {
                'status': 'insufficient_data',
                'reason': 'Product created < 30 days ago'
            }

        # DORMANT: No sales for 90+ days (even if once sold)
        if days_since_last is not None and days_since_last > 90 and sale_count > 0:
            return {
                'status': 'dormant',
                'reason': f'No sales for {days_since_last} days'
            }

        if sale_count == 0:
            return {
                'status': 'never_sold',
                'reason': 'Never been sold'
            }

        if days_since_last is None:
            return {
                'status': 'insufficient_data',
                'reason': 'Cannot determine last sale date'
            }

        # SLOW_MOVING: Sold very little in last 30 days (< 0.5 units/month)
        if qty_30d < 0.5:
            return {
                'status': 'slow_moving',
                'reason': f'Only {qty_30d} units in last 30 days'
            }

        return {
            'status': 'not_slow_moving',
            'reason': f'Regular activity ({qty_30d} units in 30 days)'
        }

    def _calculate_stock_cover(self, current_stock: float,
                               avg_daily: float, period_days: int) -> Optional[float]:
        """
        Calculate estimated days of stock at current sales velocity.

        Returns None if cannot be reliably calculated.
        Only counts COMPLETED invoices.
        """
        # Need sufficient sales history for reliable estimate
        if current_stock <= 0:
            return 0  # Already out or will run out today

        if avg_daily == 0:
            return None  # No sales, cannot estimate

        # Simple calculation: current_stock / daily_rate
        days_remaining = current_stock / avg_daily

        # Round to 1 decimal for practical use
        return round(days_remaining, 1)

    def _calculate_days_since(self, date_str: Optional[str]) -> Optional[int]:
        """Calculate days between a date string and today."""
        if not date_str:
            return None

        try:
            last_date = datetime.fromisoformat(date_str).date()
            today = date.today()
            delta = today - last_date
            return delta.days
        except (ValueError, TypeError, AttributeError):
            return None

    def _is_newly_created(self, created_at: str) -> bool:
        """Check if product was created less than 30 days ago."""
        try:
            created_date = datetime.fromisoformat(created_at).date()
            today = date.today()
            delta = today - created_date
            return delta.days < 30
        except (ValueError, TypeError, AttributeError):
            return False

    def _urgency_score(self, item: Dict) -> float:
        """
        Score product by urgency for attention.

        Higher score = more urgent. Used for sorting attention items.
        """
        score = 0.0

        # Most urgent: out of stock
        if 'OUT_OF_STOCK' in item['flags']:
            score += 1000

        # Very urgent: below reorder level
        if 'BELOW_REORDER_LEVEL' in item['flags']:
            score += 500

        # Urgent: low stock + fast moving
        if 'LOW_STOCK_FAST_MOVING' in item['flags']:
            score += 400

        # Moderately urgent: low stock cover
        if 'STOCK_COVER_LOW' in item['flags']:
            score += 200

        # Minor: dormant (takes up space, not generating revenue)
        if 'DORMANT' in item['flags']:
            score += 50

        # Minor: configuration issues
        if 'NO_REORDER_LEVEL' in item['flags']:
            score += 10

        # Minor: slow-moving or never sold
        if 'SLOW_MOVING_STOCK' in item['flags']:
            score += 5
        if 'NEVER_SOLD' in item['flags']:
            score += 5

        return score

    def _generate_summary(self, all_products: List[Dict],
                          attention_items: List[Dict]) -> Dict[str, Any]:
        """Generate summary statistics."""

        total_products = len(all_products)

        out_of_stock = [p for p in all_products if float(p['current_stock']) <= 0]
        low_stock = [p for p in all_products
                     if float(p['reorder_level']) > 0 and
                     float(p['current_stock']) <= float(p['reorder_level'])]

        # Products with recent sales activity
        active_products = [p for p in all_products if int(p['sale_count']) > 0]
        dormant_products = [p for p in all_products
                           if int(p['sale_count']) == 0]

        # Sum totals
        total_stock_units = sum(
            float(p['current_stock']) for p in all_products
        )

        return {
            'total_products': total_products,
            'active_products': len(active_products),
            'dormant_products': len(dormant_products),
            'out_of_stock_count': len(out_of_stock),
            'low_stock_count': len(low_stock),
            'products_needing_attention': len(attention_items),
            'total_stock_units': round(total_stock_units, 2),
            'status': self._compute_overall_status(len(out_of_stock), len(low_stock), total_products)
        }

    def _compute_overall_status(self, out_of_stock: int, low_stock: int,
                                total: int) -> str:
        """Compute overall inventory health status."""
        if total == 0:
            return 'no_products'

        if out_of_stock > 0:
            return 'critical'

        low_pct = (low_stock / total) * 100
        if low_pct > 20:
            return 'warning'

        if low_pct > 5:
            return 'caution'

        return 'healthy'


def get_stock_intelligence(tenant_id: str, get_conn_func) -> StockIntelligenceEngine:
    """Factory function for creating StockIntelligenceEngine instances."""
    return StockIntelligenceEngine(tenant_id, get_conn_func)
