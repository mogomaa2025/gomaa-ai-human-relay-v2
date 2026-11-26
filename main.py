import argparse
import os
from flask import Flask, jsonify, render_template, request

from core.file_manager import FileManager
from core.modes import DEFAULT_MODE, MODES
from core.patch_engine import PatchEngine
from core.prompt_builder import PromptBuilder
from core.validators import validate_syntax

app = Flask(__name__)

file_manager = FileManager(os.environ.get("PROJECT_DIR"))
prompt_builder = PromptBuilder(file_manager)
patch_engine = PatchEngine(file_manager)


@app.get("/")
def index():
    return render_template(
        "index.html",
        project_root=str(file_manager.root),
        modes=MODES,
        default_mode=DEFAULT_MODE,
    )


@app.get("/api/modes")
def api_modes():
    return jsonify({"modes": MODES, "default": DEFAULT_MODE})


@app.get("/api/files")
def api_files():
    include_ignored = request.args.get("include_ignored", "false").lower() == "true"
    return jsonify({"files": file_manager.scan_files(include_ignored=include_ignored)})


@app.get("/api/browse")
def api_browse():
    path = request.args.get("path", "")
    return jsonify(file_manager.browse_dirs(path))


@app.post("/api/project")
def api_project():
    raw_path = (request.json or {}).get("path", "")
    try:
        new_root = file_manager.set_root(raw_path)
        return jsonify({"ok": True, "root": str(new_root)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/smart_select")
def api_smart_select():
    task = (request.json or {}).get("task", "")
    return jsonify({"selected": file_manager.smart_select(task)})


@app.post("/api/build_prompt")
def api_build_prompt():
    data = request.json or {}
    mode = data.get("mode", DEFAULT_MODE)
    if mode not in MODES:
        mode = DEFAULT_MODE
    raw_chunk = data.get("chunk_size")
    chunk_size = int(raw_chunk) if raw_chunk not in (None, "") else None
    result = prompt_builder.build(
        task=data.get("task", ""),
        file_specs=data.get("files", []),
        include_tree=bool(data.get("include_tree", True)),
        chunk_size=chunk_size,
        goal_step=int(data.get("goal_step", 1)),
        answers=data.get("answers", []),
        applied_log=data.get("applied_log", []),
        mode=mode,
    )
    return jsonify(result)


@app.post("/api/apply")
def api_apply():
    data = request.json or {}
    parsed = patch_engine.parse_response(response_text=data.get("text", ""))
    ops = parsed["ops"]
    results = patch_engine.execute(ops, apply_changes=bool(data.get("apply", False))) if ops else []

    if not ops and not parsed["need_files"] and not parsed["questions"] and not parsed.get("plan"):
        results = [{
            "path": "-",
            "status": "error",
            "message": "No <<<EDIT>>>, <<<WRITE>>>, <<<NEED_FILES>>>, or <<<ASK>>> blocks found.",
        }]

    return jsonify({
        "results": results,
        "need_files": parsed["need_files"],
        "questions": parsed["questions"],
        "plan": parsed.get("plan", []),
        "status_flag": parsed["status_flag"],
    })


@app.get("/api/file")
def api_read_file():
    rel_path = request.args.get("path", "")
    try:
        content = file_manager.read_raw(rel_path)
        return jsonify({"path": rel_path, "content": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/file")
def api_save_file():
    data = request.json or {}
    rel_path = data.get("path", "")
    content = data.get("content", "")
    try:
        syntax_err = validate_syntax(rel_path, content)
        if syntax_err:
            return jsonify({"error": syntax_err}), 400
        backups = []
        file_manager.write_with_backup(rel_path, content, backups)
        file_manager.commit_undo_batch(backups)
        return jsonify({"ok": True, "path": rel_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/file/rename")
def api_rename_file():
    data = request.json or {}
    old_path = data.get("old_path", "")
    new_path = data.get("new_path", "")
    try:
        src = file_manager.safe_path(old_path)
        dst = file_manager.safe_path(new_path)
        if not src.exists():
            return jsonify({"error": f"Path not found: {old_path}"}), 400
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return jsonify({"ok": True, "old_path": old_path, "new_path": new_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/file/delete")
def api_delete_file():
    data = request.json or {}
    rel_path = data.get("path", "")
    try:
        target = file_manager.safe_path(rel_path)
        if not target.exists():
            return jsonify({"error": f"Path not found: {rel_path}"}), 400
        if target.is_dir():
            for root, dirs, files in os.walk(target, topdown=False):
                for name in files:
                    os.unlink(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            target.rmdir()
        else:
            target.unlink()
        return jsonify({"ok": True, "path": rel_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/file/create")
def api_create_item():
    data = request.json or {}
    parent_path = data.get("parent_path", "")
    name = data.get("name", "").strip()
    is_dir = bool(data.get("is_dir", False))
    try:
        new_rel = f"{parent_path}/{name}".lstrip("/") if parent_path else name
        target = file_manager.safe_path(new_rel)
        if target.exists():
            return jsonify({"error": f"Path already exists: {new_rel}"}), 400
        if is_dir:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        return jsonify({"ok": True, "path": new_rel})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/file/paste")
def api_paste_item():
    data = request.json or {}
    src_path = data.get("src_path", "")
    dest_dir = data.get("dest_dir", "")
    action = data.get("action", "copy")
    try:
        src = file_manager.safe_path(src_path)
        if not src.exists():
            return jsonify({"error": f"Source not found: {src_path}"}), 400
        
        name = src.name
        dest_rel = f"{dest_dir}/{name}".lstrip("/") if dest_dir else name
        dst = file_manager.safe_path(dest_rel)

        if dst == src or dst.is_relative_to(src):
            return jsonify({"error": "Cannot paste item into itself or subfolder."}), 400

        if src.is_dir():
            import shutil
            if dst.exists():
                return jsonify({"error": f"Destination already exists: {dest_rel}"}), 400
            shutil.copytree(src, dst)
            if action == "cut":
                shutil.rmtree(src)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(src, dst)
            if action == "cut":
                src.unlink()

        return jsonify({"ok": True, "path": dest_rel})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/undo")
def api_undo():
    count = file_manager.undo_last()
    if count == 0:
        return jsonify({"error": "Nothing to undo."}), 400
    return jsonify({"message": f"Reverted {count} file(s)."})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ChatBridge — edit projects via AI chat.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CHATBRIDGE_PORT", os.environ.get("PORT", 5000))),
        help="Port to listen on (default: CHATBRIDGE_PORT/PORT env or 5000).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("CHATBRIDGE_HOST", "127.0.0.1"),
        help="Host to bind (default: CHATBRIDGE_HOST env or 127.0.0.1).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    app.run(host=args.host, port=args.port, debug=False)