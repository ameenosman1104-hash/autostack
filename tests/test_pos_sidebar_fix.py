"""Test: POS sidebar navigation link (sidebar fix verification)."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app


def test_pos_sidebar_link_exists():
    """Verify POS link appears in authenticated user's sidebar."""
    app = create_app()
    app.config['TESTING'] = True
    
    # Verify POS blueprint endpoint is registered
    endpoints = [rule.endpoint for rule in app.url_map.iter_rules() if 'pos' in rule.endpoint]
    assert 'pos.index' in endpoints, f"pos.index endpoint not found. Endpoints: {endpoints}"
    
    # Verify template contains POS link
    template_path = os.path.join(os.path.dirname(__file__), '../app/templates/base.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()
        assert 'Point of Sale' in content, "Point of Sale link text not found in template"
        assert "url_for('pos.index')" in content, "POS url_for() not found in template"
        assert 'bi-cash-coin' in content, "POS icon (bi-cash-coin) not found in template"
    
    print("[OK] POS endpoint 'pos.index' is registered")
    print("[OK] Template contains 'Point of Sale' link text")
    print("[OK] Template uses url_for('pos.index')")
    print("[OK] Template includes cash-coin icon (bi-cash-coin)")


if __name__ == "__main__":
    test_pos_sidebar_link_exists()
