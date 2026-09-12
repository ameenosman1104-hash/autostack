"""API endpoints for local file connection pairing and synchronization."""
import json
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

bp = Blueprint("local_file_sync_api", __name__)


@bp.route("/api/local-file/preview", methods=["POST"])
@login_required
def preview_file():
    """Preview file contents to help with column mapping."""
    data = request.get_json() or {}
    file_path = data.get("file_path", "").strip()
    worksheet_name = data.get("worksheet_name")

    if not file_path:
        return jsonify({"error": "file_path required"}), 400

    try:
        from ..services.file_snapshot import read_snapshot

        # Read first few rows for preview
        rows = read_snapshot(file_path, sheet=worksheet_name)
        preview = rows[:5] if rows else []

        return jsonify({
            "preview": preview,
            "total_rows": len(rows),
            "columns": list(preview[0].keys()) if preview else []
        }), 200
    except Exception as e:
        current_app.logger.error(f"File preview failed: {e}")
        return jsonify({"error": str(e)}), 400


@bp.route("/api/local-file/pair", methods=["POST"])
@login_required
def pair_device():
    """Initiate pairing with a local file. Returns a pairing token for the device."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    name = data.get("name", "").strip()
    file_path = data.get("file_path", "").strip()
    worksheet_name = data.get("worksheet_name")
    column_mapping = data.get("column_mapping", {})
    unique_key_field = data.get("unique_key_field", "Invoice No.")

    if not name or not file_path:
        return jsonify({"error": "name and file_path required"}), 400

    try:
        from ..services.local_file_sync import create_local_connection
        pairing_token = create_local_connection(tid, name, file_path, worksheet_name, column_mapping, unique_key_field)

        return jsonify({
            "pairing_token": pairing_token,
            "message": "Pairing initiated. Use this token to connect the Windows background connector."
        }), 201
    except Exception as e:
        current_app.logger.error(f"Local file pairing failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/local-file/upload", methods=["POST"])
def upload_snapshot():
    """Device uploads a file snapshot for sync. Authenticated by pairing token."""
    pairing_token = request.headers.get("X-Pairing-Token", "").strip()
    if not pairing_token:
        return jsonify({"error": "Missing pairing token"}), 401

    data = request.get_json() or {}
    snapshot = data.get("snapshot", [])
    device_id = data.get("device_id", "unknown")
    device_name = data.get("device_name", "Unknown Device")

    if not isinstance(snapshot, list):
        return jsonify({"error": "snapshot must be an array"}), 400

    try:
        from ..services.local_file_sync import lookup_tenant_by_token, get_connection_by_token, update_sync_status
        from ..services.excel_sync import detect_and_apply_changes

        # Look up tenant and connection from pairing token
        tid, connection_id = lookup_tenant_by_token(pairing_token)
        if not tid:
            current_app.logger.warning(f"Invalid or revoked pairing token")
            return jsonify({"error": "Invalid or revoked pairing token"}), 401

        # Fetch connection metadata
        conn_data = get_connection_by_token(tid, pairing_token)
        if not conn_data:
            return jsonify({"error": "Connection not found"}), 404

        # Build config dict for detect_and_apply_changes
        config = {
            "source": "local_file",
            "unique_key": conn_data["unique_key_field"]
        }
        mapping = conn_data["column_mapping"]

        # Transform snapshot: file_snapshot.py returns rows with field names (invoice, name, email)
        # but detect_and_apply_changes expects rows with column names from the mapping.
        # Inverse-map field names back to column names.
        snapshot_transformed = []
        for row in snapshot:
            transformed_row = {}
            for field, value in row.items():
                # Find which column name this field maps to
                col_name = mapping.get(field, field)
                transformed_row[col_name] = value
            snapshot_transformed.append(transformed_row)

        # Apply changes to debtors
        stats = detect_and_apply_changes(
            tid,
            "local_file",
            config,
            mapping,
            conn_data["unique_key_field"],
            snapshot_data=snapshot_transformed,
            external_source=f"local_file:{connection_id}"
        )

        # Update sync status
        update_sync_status(tid, connection_id, "success")

        return jsonify({
            "status": "success",
            "synced_rows": len(snapshot),
            "added": stats.get("added", 0),
            "updated": stats.get("updated", 0),
            "removed": stats.get("removed", 0),
            "errors": stats.get("errors", [])
        }), 200

    except Exception as e:
        current_app.logger.error(f"Local file upload failed: {e}")
        try:
            from ..services.local_file_sync import lookup_tenant_by_token, update_sync_status
            tid, _ = lookup_tenant_by_token(pairing_token)
            if tid:
                update_sync_status(tid, _, "error", str(e))
        except:
            pass
        return jsonify({"error": str(e)}), 500


@bp.route("/api/local-file/status", methods=["GET"])
@login_required
def get_sync_status():
    """Get status of all local file connections for the current user."""
    tid = current_user.tenant_id

    try:
        from ..services.local_file_sync import list_connections, get_unresolved_conflicts

        connections = list_connections(tid)
        conflicts = get_unresolved_conflicts(tid)

        return jsonify({
            "connections": connections,
            "conflicts": conflicts,
            "conflict_count": len(conflicts)
        }), 200
    except Exception as e:
        current_app.logger.error(f"Status check failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/local-file/resolve-conflict", methods=["POST"])
@login_required
def resolve_conflict():
    """Resolve a conflict: keep AutoStack value or use source value."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    conflict_id = data.get("conflict_id")
    resolution = data.get("resolution")

    if not conflict_id or resolution not in ("keep_autostack", "use_source"):
        return jsonify({"error": "conflict_id and resolution (keep_autostack|use_source) required"}), 400

    try:
        from ..services.local_file_sync import resolve_conflict

        if not resolve_conflict(tid, conflict_id, resolution):
            return jsonify({"error": "Conflict not found"}), 404

        # TODO: Apply the resolved value to the debtor if resolution is 'use_source'
        return jsonify({"message": "Conflict resolved"}), 200
    except Exception as e:
        current_app.logger.error(f"Conflict resolution failed: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/local-file/revoke", methods=["POST"])
@login_required
def revoke_connection():
    """Revoke a device connection. Device can no longer sync."""
    tid = current_user.tenant_id
    data = request.get_json() or {}

    connection_id = data.get("connection_id")
    if not connection_id:
        return jsonify({"error": "connection_id required"}), 400

    try:
        from ..services.local_file_sync import revoke_connection

        if not revoke_connection(tid, connection_id):
            return jsonify({"error": "Connection not found"}), 404

        return jsonify({"message": "Connection revoked"}), 200
    except Exception as e:
        current_app.logger.error(f"Revocation failed: {e}")
        return jsonify({"error": str(e)}), 500
