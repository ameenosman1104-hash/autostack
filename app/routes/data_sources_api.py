"""API endpoints for unified data source management (local, hosted URL, API)."""
import json
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

bp = Blueprint("data_sources_api", __name__)


@bp.route("/api/data-sources/preview", methods=["POST"])
@login_required
def preview_source():
    """Preview data source to confirm columns."""
    data = request.get_json() or {}
    source_type = data.get("source_type", "").strip()
    config = data.get("config", {})

    if not source_type:
        return jsonify({"error": "source_type required"}), 400

    try:
        if source_type == "local_file":
            from ..services.file_snapshot import read_snapshot
            rows = read_snapshot(config.get("file_path"), sheet=config.get("worksheet_name"))
            preview = rows[:5] if rows else []
            return jsonify({
                "preview": preview,
                "total_rows": len(rows),
                "columns": list(preview[0].keys()) if preview else []
            }), 200

        elif source_type in ("hosted_url", "api"):
            from ..services.excel_sync import fetch_source_data
            headers, rows = fetch_source_data(source_type, config)
            if rows is None:
                return jsonify({"error": "Failed to fetch data"}), 400
            preview = rows[:5] if rows else []
            return jsonify({
                "preview": preview,
                "total_rows": len(rows),
                "columns": list(preview[0].keys()) if preview else []
            }), 200

        else:
            return jsonify({"error": f"Unsupported source type: {source_type}"}), 400

    except Exception as e:
        current_app.logger.error(f"Preview failed: {e}")
        return jsonify({"error": str(e)}), 400


@bp.route("/api/data-sources/connect", methods=["POST"])
@login_required
def connect_source():
    """Create a new data source connection."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    name = data.get("name", "").strip()
    source_type = data.get("source_type", "").strip()
    destination = data.get("destination", "debtors").strip()
    config = data.get("config", {})
    column_mapping = data.get("column_mapping", {})
    unique_key_field = data.get("unique_key_field", "Invoice No.")
    matching_identifier = data.get("matching_identifier", "auto").strip()

    if not name or not source_type or destination not in ("stock", "debtors"):
        return jsonify({"error": "name, source_type, and valid destination required"}), 400

    try:
        from ..services.data_sources import create_data_source

        pairing_token, source_id = create_data_source(
            tid, name, source_type, config, column_mapping, unique_key_field, destination, matching_identifier
        )

        response = {
            "source_id": source_id,
            "source_type": source_type,
            "name": name
        }

        if pairing_token:
            response["pairing_token"] = pairing_token
            response["message"] = "Use this token with the Windows connector"

        return jsonify(response), 201

    except Exception as e:
        current_app.logger.error(f"Connection failed: {e}")
        return jsonify({"error": str(e)}), 400


@bp.route("/api/data-sources/list", methods=["GET"])
@login_required
def list_sources():
    """List all data sources for current tenant."""
    tid = current_user.tenant_id

    try:
        from ..services.data_sources import list_data_sources, get_unresolved_conflicts

        # Support filtering by destination: /api/data-sources/list?destination=stock or debtors
        destination = request.args.get("destination", "").strip()
        if destination and destination not in ("stock", "debtors"):
            return jsonify({"error": "Invalid destination"}), 400

        sources = list_data_sources(tid, destination=destination if destination else None)
        conflicts = get_unresolved_conflicts(tid)

        return jsonify({
            "sources": sources,
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "destination_filter": destination if destination else "all"
        }), 200

    except Exception as e:
        current_app.logger.error(f"List failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/data-sources/sync-now", methods=["POST"])
@login_required
def sync_now():
    """Trigger immediate sync for a data source (stock or debtors)."""
    tid = current_user.tenant_id
    data = request.get_json() or {}
    source_id = data.get("source_id")
    destination = data.get("destination")  # Optional: stock or debtors

    if not source_id:
        return jsonify({"error": "source_id required"}), 400

    try:
        from ..services.data_sources import get_data_source, update_sync_status
        from ..services.excel_sync import (fetch_source_data, detect_and_apply_changes,
                                          detect_and_apply_stock_changes)

        source = get_data_source(tid, source_id)
        if not source:
            return jsonify({"error": "Source not found"}), 404

        # Use source's destination if not explicitly provided
        if not destination:
            destination = source.get("destination", "debtors")

        source_type = source["source_type"]
        config = source["config"]
        mapping = source["column_mapping"]
        unique_key = source["unique_key_field"]

        # For local files: return error (managed by connector)
        if source_type == "local_file":
            return jsonify({
                "error": "Local file sync is managed by the Windows connector",
                "note": "Ensure the connector is running on the target computer"
            }), 400

        # For hosted URL and API: fetch and sync
        if source_type in ("hosted_url", "api"):
            headers, rows = fetch_source_data(source_type, config)
            if rows is None:
                return jsonify({"error": "Failed to fetch from source"}), 500

            # Transform field names to column names
            snapshot_transformed = []
            for row in rows:
                transformed_row = {}
                for field, value in row.items():
                    col_name = mapping.get(field, field)
                    transformed_row[col_name] = value
                snapshot_transformed.append(transformed_row)

            # Use appropriate sync function based on destination
            if destination == "stock":
                stats = detect_and_apply_stock_changes(
                    tid, source_type, config, mapping, unique_key,
                    snapshot_data=snapshot_transformed,
                    external_source=f"{source_type}:{source_id}"
                )
            else:  # debtors (default)
                stats = detect_and_apply_changes(
                    tid, source_type, config, mapping, unique_key,
                    snapshot_data=snapshot_transformed,
                    external_source=f"{source_type}:{source_id}"
                )

            update_sync_status(tid, source_id, "success")

            return jsonify({
                "status": "success",
                "destination": destination,
                "synced_rows": len(snapshot_transformed),
                "added": stats.get("added", 0),
                "updated": stats.get("updated", 0),
                "removed": stats.get("removed", 0),
                "errors": stats.get("errors", [])
            }), 200

        return jsonify({"error": "Unsupported source type"}), 400

    except Exception as e:
        current_app.logger.error(f"Sync failed: {e}")
        try:
            from ..services.data_sources import update_sync_status
            update_sync_status(tid, source_id, "error", str(e))
        except:
            pass
        return jsonify({"error": str(e)}), 500


@bp.route("/api/data-sources/resolve-conflict", methods=["POST"])
@login_required
def resolve_conflict():
    """Resolve a conflict."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    conflict_id = data.get("conflict_id")
    resolution = data.get("resolution")

    if not conflict_id or resolution not in ("keep_autostack", "use_source"):
        return jsonify({"error": "conflict_id and resolution required"}), 400

    try:
        from ..services.data_sources import resolve_conflict

        if not resolve_conflict(tid, conflict_id, resolution):
            return jsonify({"error": "Conflict not found"}), 404

        return jsonify({"message": "Conflict resolved"}), 200

    except Exception as e:
        current_app.logger.error(f"Resolution failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/data-sources/disconnect", methods=["POST"])
@login_required
def disconnect():
    """Disconnect a data source."""
    tid = current_user.tenant_id
    data = request.get_json() or {}
    source_id = data.get("source_id")

    if not source_id:
        return jsonify({"error": "source_id required"}), 400

    try:
        from ..services.data_sources import disconnect_source

        if not disconnect_source(tid, source_id):
            return jsonify({"error": "Source not found"}), 404

        return jsonify({"message": "Source disconnected"}), 200

    except Exception as e:
        current_app.logger.error(f"Disconnect failed: {e}")
        return jsonify({"error": str(e)}), 500


# Device upload endpoint for local file connector
@bp.route("/api/data-sources/upload", methods=["POST"])
def upload_snapshot():
    """Device uploads file snapshot (local connector only)."""
    pairing_token = request.headers.get("X-Pairing-Token", "").strip()
    if not pairing_token:
        return jsonify({"error": "Missing pairing token"}), 401

    data = request.get_json() or {}
    snapshot = data.get("snapshot", [])
    device_id = data.get("device_id", "unknown")
    device_name = data.get("device_name", "Unknown")

    if not isinstance(snapshot, list):
        return jsonify({"error": "snapshot must be array"}), 400

    try:
        from ..services.data_sources import lookup_tenant_by_token, get_data_source, update_sync_status
        from ..services.excel_sync import detect_and_apply_changes

        tid, source_id = lookup_tenant_by_token(pairing_token)
        if not tid:
            return jsonify({"error": "Invalid or revoked token"}), 401

        source = get_data_source(tid, source_id)
        if not source:
            return jsonify({"error": "Source not found"}), 404

        config = source["config"]
        mapping = source["column_mapping"]
        unique_key = source["unique_key_field"]

        # Transform field names to column names
        snapshot_transformed = []
        for row in snapshot:
            transformed_row = {}
            for field, value in row.items():
                col_name = mapping.get(field, field)
                transformed_row[col_name] = value
            snapshot_transformed.append(transformed_row)

        stats = detect_and_apply_changes(
            tid, "local_file", config, mapping, unique_key,
            snapshot_data=snapshot_transformed,
            external_source=f"local_file:{source_id}"
        )

        update_sync_status(tid, source_id, "success")

        return jsonify({
            "status": "success",
            "synced_rows": len(snapshot),
            "added": stats.get("added", 0),
            "updated": stats.get("updated", 0),
            "removed": stats.get("removed", 0),
            "errors": stats.get("errors", [])
        }), 200

    except Exception as e:
        current_app.logger.error(f"Upload failed: {e}")
        try:
            from ..services.data_sources import lookup_tenant_by_token, update_sync_status
            tid, source_id = lookup_tenant_by_token(pairing_token)
            if tid and source_id:
                update_sync_status(tid, source_id, "error", str(e))
        except:
            pass
        return jsonify({"error": str(e)}), 500


@bp.route("/api/connections", methods=["GET"])
@login_required
def list_connections():
    """List saved connections for current user's tenant and specified module."""
    tid = current_user.tenant_id
    module = request.args.get("module", "debtors").strip()  # stock or debtors

    if module not in ("stock", "debtors"):
        return jsonify({"error": "Invalid module"}), 400

    try:
        from ..services.data_sources import list_data_sources
        sources = list_data_sources(tid, destination=module)

        # Return connection list with safe info (no credentials)
        connections = []
        for s in sources:
            source_type = s.get("source_type", "")
            config = s.get("config") or {}

            # Build safe display info
            if source_type == "local_file":
                source_info = s.get("file_path", "").split("\\")[-1] if s.get("file_path") else "Local File"
                if s.get("worksheet_name"):
                    source_info += f" ({s['worksheet_name']})"
            elif source_type == "hosted_url":
                url = config.get("url", "")
                source_info = url[:50] + "..." if len(url) > 50 else url
            elif source_type == "api":
                url = config.get("api_url", "")
                source_info = url[:50] + "..." if len(url) > 50 else url
            else:
                source_info = "Unknown"

            connections.append({
                "id": s["id"],
                "name": s["name"],
                "type": source_type,
                "source": source_info,
                "last_sync": s.get("last_sync_at"),
                "status": s.get("last_sync_status", "pending")
            })

        return jsonify({"connections": connections}), 200
    except Exception as e:
        current_app.logger.error(f"List connections failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/connections/{id}/test", methods=["POST"])
@login_required
def test_connection(id):
    """Test a connection and preview data without saving."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    try:
        from ..services.data_sources import get_data_source
        from ..services.excel_sync import fetch_source_data

        # If updating config: use provided config, else get from DB
        source_type = data.get("source_type")
        config = data.get("config", {})

        if not source_type or not config:
            # Load existing connection
            source = get_data_source(tid, id)
            if not source:
                return jsonify({"error": "Connection not found"}), 404
            source_type = source["source_type"]
            config = source.get("config") or {}

        # Validate based on type
        if source_type == "local_file":
            from ..services.file_snapshot import read_snapshot
            rows = read_snapshot(config.get("file_path"), sheet=config.get("worksheet_name"))
            if not rows:
                return jsonify({"error": "File is empty or unreadable"}), 400

            preview = rows[:3] if rows else []
            return jsonify({
                "ok": True,
                "total_rows": len(rows),
                "preview": preview,
                "columns": list(preview[0].keys()) if preview else []
            }), 200

        elif source_type in ("hosted_url", "api"):
            headers, rows = fetch_source_data(source_type, config)
            if rows is None:
                return jsonify({"error": "Failed to fetch from source"}), 400

            if not rows:
                return jsonify({"error": "Source returned no data"}), 400

            preview = rows[:3] if rows else []
            return jsonify({
                "ok": True,
                "total_rows": len(rows),
                "preview": preview,
                "columns": list(preview[0].keys()) if preview else []
            }), 200

        else:
            return jsonify({"error": f"Unsupported source type: {source_type}"}), 400

    except Exception as e:
        current_app.logger.error(f"Test connection failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/api/connections", methods=["POST"])
@login_required
def save_connection():
    """Save a new data source connection (file or API)."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    name = data.get("name", "").strip()
    source_type = data.get("source_type", "").strip()
    destination = data.get("destination", "debtors").strip()
    config = data.get("config", {})
    column_mapping = data.get("column_mapping", {})
    unique_key_field = data.get("unique_key_field", "Invoice No.")

    if not name or not source_type or destination not in ("stock", "debtors"):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        from ..services.data_sources import create_data_source

        pairing_token, source_id = create_data_source(
            tid, name, source_type, config, column_mapping, unique_key_field,
            destination, "auto"
        )

        return jsonify({
            "ok": True,
            "connection_id": source_id,
            "pairing_token": pairing_token if pairing_token else None
        }), 201

    except Exception as e:
        current_app.logger.error(f"Save connection failed: {e}")
        return jsonify({"error": str(e)}), 400


@bp.route("/api/connections/{id}", methods=["DELETE"])
@login_required
def delete_connection(id):
    """Delete a saved connection."""
    tid = current_user.tenant_id

    try:
        from ..services.data_sources import disconnect_source
        disconnect_source(tid, id)
        return jsonify({"ok": True, "message": "Connection removed"}), 200
    except Exception as e:
        current_app.logger.error(f"Delete connection failed: {e}")
        return jsonify({"error": str(e)}), 400
