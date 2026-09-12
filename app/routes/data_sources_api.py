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
    config = data.get("config", {})
    column_mapping = data.get("column_mapping", {})
    unique_key_field = data.get("unique_key_field", "Invoice No.")

    if not name or not source_type:
        return jsonify({"error": "name and source_type required"}), 400

    try:
        from ..services.data_sources import create_data_source

        pairing_token, source_id = create_data_source(
            tid, name, source_type, config, column_mapping, unique_key_field
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

        sources = list_data_sources(tid)
        conflicts = get_unresolved_conflicts(tid)

        return jsonify({
            "sources": sources,
            "conflicts": conflicts,
            "conflict_count": len(conflicts)
        }), 200

    except Exception as e:
        current_app.logger.error(f"List failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/data-sources/sync-now", methods=["POST"])
@login_required
def sync_now():
    """Trigger immediate sync for a data source."""
    tid = current_user.tenant_id
    data = request.get_json() or {}
    source_id = data.get("source_id")

    if not source_id:
        return jsonify({"error": "source_id required"}), 400

    try:
        from ..services.data_sources import get_data_source
        from ..services.excel_sync import fetch_source_data, detect_and_apply_changes

        source = get_data_source(tid, source_id)
        if not source:
            return jsonify({"error": "Source not found"}), 404

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

            stats = detect_and_apply_changes(
                tid, source_type, config, mapping, unique_key,
                snapshot_data=snapshot_transformed,
                external_source=f"{source_type}:{source_id}"
            )

            from ..services.data_sources import update_sync_status
            update_sync_status(tid, source_id, "success")

            return jsonify({
                "status": "success",
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
