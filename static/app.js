const App = {
  files: [],
  selected: new Map(), // path -> { path, mode, start, end, tokens }
  expandedDirs: new Set(),
  browseParent: "",
  goalStep: 1,
  goalFinished: false,
  stage: "goal", // "goal" | "paste"
  appliedLog: [],
  pendingQuestions: [],
  planTasks: [],
  editingPath: "",
  lastCopiedPrompt: "",
  lastCapturedText: "",
  lastFailedErrors: [],
  isProcessing: false,
  ctxPath: "",
  mode: localStorage.getItem("chatbridge.mode") || "gemini",
  jsonParts: [],
  modeMeta: {},

  setLedState(healthy, errorSummary = "") {
    const gLed = document.getElementById("greenLed");
    const rLed = document.getElementById("redLed");
    const rLbl = document.getElementById("redLedLabel");
    if (!gLed || !rLed) return;

    if (healthy) {
      gLed.classList.add("on");
      rLed.classList.remove("on");
      rLed.title = "No failures";
      if (rLbl) rLbl.classList.add("hidden");
    } else {
      gLed.classList.remove("on");
      rLed.classList.add("on");
      rLed.title = `Failure detected: ${errorSummary}. Click to self-heal & continue!`;
      if (rLbl) {
        rLbl.textContent = "Failure — Click to Self-Heal";
        rLbl.classList.remove("hidden");
      }
    }
  },

  openErrorModal() {
    const box = document.getElementById("errorLogContent");
    box.innerHTML = "";
    if (!this.appliedLog.length && !this.lastFailedErrors.length) {
      box.innerHTML = `<div class="muted" style="padding: 12px;">No error logs or self-heal events recorded yet.</div>`;
    } else {
      this.appliedLog.forEach((log) => {
        const item = document.createElement("div");
        item.className = "dline meta";
        item.textContent = log;
        box.appendChild(item);
      });
      this.lastFailedErrors.forEach((err) => {
        const item = document.createElement("div");
        item.className = "dline del";
        item.textContent = `ERROR [${err.path}]: ${err.message}`;
        box.appendChild(item);
      });
    }
    document.getElementById("errorModal").classList.remove("hidden");
  },

  closeErrorModal() {
    document.getElementById("errorModal").classList.add("hidden");
  },

  async selfHeal() {
    if (!this.lastFailedErrors.length) return;
    const failedPaths = [...new Set(this.lastFailedErrors.map((e) => e.path).filter((p) => p && p !== "-"))];
    const errNotes = this.lastFailedErrors.map((e) => `${e.path}: ${e.message}`).join("; ");

    failedPaths.forEach((p) => {
      const match = this.files.find((f) => f.path === p);
      this.selected.set(p, {
        path: p,
        mode: "full",
        start: "",
        end: "",
        tokens: match ? match.est_tokens : 500,
      });
    });

    this.appliedLog.push(`SELF-HEAL RECOVERY (Step ${this.goalStep}): Previous patch failed (${errNotes}). Sending full file contents so AI can re-generate exact edits.`);
    this.lastFailedErrors = [];
    this.setLedState(true);
    this.renderFiles();
    this.renderChips();

    this.goalStep += 1;
    await this.buildPrompt(true);
    document.getElementById("aiResponse").value = "";
  },

  showPopup({ type = "info", title = "", message = "", actions = [] }) {
    const box = document.getElementById("inlinePopup");
    const iconEl = document.getElementById("inlinePopupIcon");
    const titleEl = document.getElementById("inlinePopupTitle");
    const msgEl = document.getElementById("inlinePopupMsg");
    const actEl = document.getElementById("inlinePopupActions");
    if (!box) return;

    box.className = `inline-popup ${type}`;
    const icons = { info: "ℹ️", success: "✅", red: "🚨" };
    iconEl.textContent = icons[type] || "ℹ️";
    titleEl.textContent = title || (type === "red" ? "Action Required: Paste in AI Chat" : "Status");
    msgEl.textContent = message;

    actEl.innerHTML = "";
    actions.forEach((act) => {
      const b = document.createElement("button");
      b.className = `btn ${act.cls || "primary"}`;
      b.textContent = act.label;
      b.onclick = act.onClick;
      actEl.appendChild(b);
    });
  },

  toast(msg, type = "info", actions = []) {
    this.showPopup({
      type,
      title: type === "success" ? "Completed" : (type === "red" ? "Self-Heal Diagnostic Alert" : "Status Update"),
      message: msg,
      actions: actions,
    });
  },

  initModePicker() {
    const picker = document.getElementById("modePicker");
    if (!picker) return;
    if (!picker.querySelector(`option[value="${this.mode}"]`)) {
      this.mode = picker.options[0] ? picker.options[0].value : "gemini";
    }
    picker.value = this.mode;
    this.updateModeMeta();
    picker.onchange = () => {
      this.mode = picker.value;
      localStorage.setItem("chatbridge.mode", this.mode);
      this.updateModeMeta();
      this.renderModeWarning();
      this.toggleJsonButton();
    };
  },

  updateModeMeta() {
    const picker = document.getElementById("modePicker");
    const opt = picker && picker.selectedOptions && picker.selectedOptions[0];
    if (!opt) {
      this.modeMeta = {};
      return;
    }
    this.modeMeta = {
      max: parseInt(opt.dataset.max || "0", 10),
      format: opt.dataset.format || "text",
      label: opt.textContent.trim(),
    };
  },

  getModeLimit() {
    if (this.modeMeta && this.modeMeta.max) return this.modeMeta.max;
    const picker = document.getElementById("modePicker");
    const opt = picker && picker.selectedOptions && picker.selectedOptions[0];
    return opt ? parseInt(opt.dataset.max || "0", 10) : 0;
  },

  toggleJsonButton() {
    const btn = document.getElementById("copyJsonBtn");
    if (!btn) return;
    const isJson = (this.modeMeta.format || "text") === "json_parts";
    btn.classList.toggle("hidden", !isJson);
    if (!isJson) {
      const panel = document.getElementById("jsonPartsPanel");
      if (panel) panel.classList.add("hidden");
    }
  },

  renderModeWarning(data) {
    const box = document.getElementById("modeLimitWarning");
    if (!box) return;
    const chars = data ? data.chars : 0;
    const max = data ? data.max_input_chars : this.getModeLimit();
    if (data && data.over_limit) {
      const n = (data.parts || []).length;
      const label = (data.mode || this.mode || "").toString();
      box.textContent = `Warning: prompt is ${chars.toLocaleString()} chars — exceeds ${label} limit of ${max.toLocaleString()}. It will be sent in ${n} part(s).`;
      box.classList.remove("hidden");
    } else {
      box.textContent = "";
      box.classList.add("hidden");
    }
  },

  openOptionsModal() {
    document.getElementById("optionsModal").classList.remove("hidden");
  },

  closeOptionsModal() {
    document.getElementById("optionsModal").classList.add("hidden");
  },

  openChoiceModal({ title, description = "", options = [] }) {
    document.getElementById("choiceModalTitle").textContent = title;
    document.getElementById("choiceModalDesc").textContent = description;
    const body = document.getElementById("choiceModalBody");
    body.innerHTML = "";

    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.className = "choice-option-btn";
      const lbl = document.createElement("span");
      lbl.textContent = opt.label;
      const hint = document.createElement("span");
      hint.className = "muted";
      hint.textContent = opt.hint || "";
      btn.append(lbl, hint);
      btn.onclick = () => {
        this.closeChoiceModal();
        if (opt.onSelect) opt.onSelect();
      };
      body.appendChild(btn);
    });

    document.getElementById("choiceModal").classList.remove("hidden");
  },

  closeChoiceModal() {
    document.getElementById("choiceModal").classList.add("hidden");
  },

  setGoalBadge(stateText, cls = "") {
    const b = document.getElementById("goalBadge");
    b.textContent = `Goal: Step ${this.goalStep} (${stateText})`;
    b.className = `badge goal ${cls}`;
  },

  setStage(stage) {
    this.stage = stage;
    const promptCard = document.getElementById("stagePromptCard");
    const pasteCard = document.getElementById("stagePasteCard");

    if (stage === "goal") {
      promptCard.classList.remove("hidden");
      pasteCard.classList.add("hidden");
    } else {
      promptCard.classList.add("hidden");
      pasteCard.classList.remove("hidden");
      const pasteArea = document.getElementById("pasteInputArea");
      if (this.goalFinished) {
        pasteArea.classList.add("hidden");
      } else {
        pasteArea.classList.remove("hidden");
      }
    }
  },

  resetGoal() {
    this.goalStep = 1;
    this.goalFinished = false;
    this.appliedLog = [];
    this.pendingQuestions = [];
    this.planTasks = [];
    this.lastFailedErrors = [];
    this.lastCopiedPrompt = "";
    this.lastCapturedText = "";
    this.clipboard = { path: "", action: "" };
    this.selected.clear();

    document.getElementById("task").value = "";
    document.getElementById("generatedPrompt").value = "";
    document.getElementById("promptMeta").textContent = "";
    document.getElementById("copyButtons").innerHTML = "";
    document.getElementById("questionsOut").innerHTML = "";
    document.getElementById("diffOut").innerHTML = "";
    document.getElementById("aiResponse").value = "";
    document.getElementById("applyMeta").textContent = "";

    this.setLedState(true);
    this.renderPlan([]);
    this.renderFiles();
    this.renderChips();
    this.setGoalBadge("IDLE");
    this.setStage("goal");
    this.showPopup({
      type: "info",
      title: "Gomaa Relay Ready",
      message: "Type your goal or use @mentions in the text box. Click the 🧠 brain icon for Smart Select.",
    });
    document.getElementById("task").focus();
  },

  showIgnored: false,
  clipboard: { path: "", action: "" }, // { path, action: "copy" | "cut" }

  async loadFiles() {
    try {
      const res = await fetch(`/api/files?include_ignored=${this.showIgnored}`);
      if (!res.ok) {
        let msg = "Failed to load files";
        try { const d = await res.json(); if (d.error) msg = d.error; } catch (e) {}
        return this.toast(msg, "red");
      }
      const data = await res.json();
      this.files = data.files || [];
      this.renderFiles();
    } catch (e) {
      this.toast("Failed to load files: connection error", "red");
    }
  },

  toggleIgnored() {
    this.showIgnored = !this.showIgnored;
    const btn = document.getElementById("gitIgnoreToggleBtn");
    if (btn) {
      btn.style.color = this.showIgnored ? "#38bdf8" : "inherit";
      btn.title = this.showIgnored ? "Hide .gitignore files" : "Show .gitignore files";
    }
    this.loadFiles();
    this.toast(this.showIgnored ? "Showing ignored files (.gitignore)" : "Hiding ignored files", "info");
  },

  async createNewItem(parentPath, isDir) {
    const typeLabel = isDir ? "Folder" : "File";
    const name = prompt(`Enter new ${typeLabel} name (relative to ${parentPath || "root"}):`);
    if (!name || !name.trim()) return;

    const res = await fetch("/api/file/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_path: parentPath, name: name.trim(), is_dir: isDir }),
    });
    const data = await res.json();
    if (!res.ok) return this.toast(data.error || `Failed to create ${typeLabel}`, "red");
    await this.loadFiles();
    this.toast(`Created ${typeLabel}: ${data.path}`, "success");
    if (!isDir) {
      this.openEditor(data.path);
    }
  },

  ctxNewFile() {
    const target = this.ctxPath;
    const isFile = this.files.some((f) => f.path === target);
    const parent = isFile
      ? (target.includes("/") ? target.substring(0, target.lastIndexOf("/")) : "")
      : target;
    this.createNewItem(parent, false);
  },

  ctxNewFolder() {
    const target = this.ctxPath;
    const isFile = this.files.some((f) => f.path === target);
    const parent = isFile
      ? (target.includes("/") ? target.substring(0, target.lastIndexOf("/")) : "")
      : target;
    this.createNewItem(parent, true);
  },

  ctxCopy() {
    this.clipboard = { path: this.ctxPath, action: "copy" };
    this.toast(`Copied to clipboard: ${this.ctxPath}`, "success");
  },

  ctxCut() {
    this.clipboard = { path: this.ctxPath, action: "cut" };
    this.toast(`Cut to clipboard: ${this.ctxPath}`, "success");
  },

  async ctxPaste() {
    if (!this.clipboard.path) return;
    const target = this.ctxPath;
    const isFile = this.files.some((f) => f.path === target);
    const destDir = isFile
      ? (target.includes("/") ? target.substring(0, target.lastIndexOf("/")) : "")
      : target;
    const res = await fetch("/api/file/paste", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ src_path: this.clipboard.path, dest_dir: destDir, action: this.clipboard.action }),
    });
    const data = await res.json();
    if (!res.ok) return this.toast(data.error || "Paste failed", "red");
    if (this.clipboard.action === "cut") {
      this.clipboard = { path: "", action: "" };
    }
    await this.loadFiles();
    this.toast(`Successfully pasted into ${destDir || "root"}`, "success");
  },

  async setProject(customPath = null) {
    const path = customPath !== null ? customPath : document.getElementById("projPath").value;
    const res = await fetch("/api/project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok) {
      this.setLedState(false, data.error);
      return this.toast(data.error, "red");
    }
    document.getElementById("projPath").value = data.root;
    this.expandedDirs.clear();
    this.resetGoal();
    await this.loadFiles();
  },

  async smartSelect() {
    if (this.goalFinished) return;
    const task = document.getElementById("task").value.trim();
    if (!task) return this.toast("Type a goal first, then click 🧠 Smart Select.");
    const res = await fetch("/api/smart_select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task }),
    });
    if (!res.ok) {
      let msg = "Smart Select failed";
      try { const d = await res.json(); if (d.error) msg = d.error; } catch (e) {}
      return this.toast(msg, "red");
    }
    const data = await res.json();
    this.selected.clear();
    (data.selected || []).forEach((s) => {
      this.selected.set(s.path, {
        path: s.path,
        mode: s.mode || "full",
        start: s.start || "",
        end: s.end || "",
        tokens: s.tokens || 500,
      });
    });
    this.renderFiles();
    this.renderChips();
    this.toast(`Smart Select picked ${this.selected.size} file(s)`, "success");
  },

  async openBrowser() {
    document.getElementById("browserModal").classList.remove("hidden");
    await this.browseTo(document.getElementById("projPath").value);
  },

  closeBrowser() {
    document.getElementById("browserModal").classList.add("hidden");
  },

  async browseTo(path) {
    try {
      const res = await fetch(`/api/browse?path=${encodeURIComponent(path || "")}`);
      const data = await res.json();
      if (!res.ok) {
        return this.toast(data.error || "Invalid directory path", "red");
      }
      document.getElementById("browsePath").value = data.current;
      this.browseParent = data.parent || "";
      document.getElementById("upBtn").disabled = !this.browseParent;
      document.getElementById("browserMeta").textContent = data.current;

      const drives = document.getElementById("driveBar");
      drives.innerHTML = "";
      (data.drives || []).forEach((d) => {
        const b = document.createElement("button");
        b.className = "btn ghost";
        b.style.padding = "4px 10px";
        b.textContent = `💾 ${d}`;
        b.onclick = () => this.browseTo(d);
        drives.appendChild(b);
      });

      const list = document.getElementById("dirList");
      list.innerHTML = "";
      if (!data.dirs || data.dirs.length === 0) {
        list.innerHTML = `<div class="muted" style="padding: 12px; text-align: center;">No subdirectories found or permission restricted.</div>`;
        return;
      }

      (data.dirs || []).forEach((d) => {
        const row = document.createElement("div");
        row.className = "fitem";
        row.style.padding = "6px 8px";
        row.title = "Click to open, double-click to select as root directly";
        const nameSpan = document.createElement("span");
        nameSpan.style.flex = "1";
        nameSpan.textContent = `📁 ${d.name}`;
        const hint = document.createElement("span");
        hint.className = "muted";
        hint.style.fontSize = "11px";
        hint.textContent = "Open →";
        row.append(nameSpan, hint);
        row.onclick = () => this.browseTo(d.path);
        row.ondblclick = () => {
          document.getElementById("browsePath").value = d.path;
          this.selectBrowsed();
        };
        list.appendChild(row);
      });
    } catch (e) {
      this.toast("Failed to browse directory", "red");
    }
  },

  browseUp() {
    if (this.browseParent) this.browseTo(this.browseParent);
  },

  async selectBrowsed() {
    const chosen = document.getElementById("browsePath").value;
    this.closeBrowser();
    await this.setProject(chosen);
  },

  buildFileTree(files) {
    const root = { name: "", path: "", folders: new Map(), files: [] };
    files.forEach((f) => {
      const parts = f.path.split("/");
      let node = root;
      for (let i = 0; i < parts.length - 1; i++) {
        const seg = parts[i];
        const nextPath = node.path ? `${node.path}/${seg}` : seg;
        if (!node.folders.has(seg)) {
          node.folders.set(seg, { name: seg, path: nextPath, folders: new Map(), files: [] });
        }
        node = node.folders.get(seg);
      }
      node.files.push({ ...f, name: parts[parts.length - 1] });
    });
    return root;
  },

  collectFolderFiles(node, acc = []) {
    node.files.forEach((f) => acc.push(f));
    node.folders.forEach((sub) => this.collectFolderFiles(sub, acc));
    return acc;
  },

  renderTreeNode(node, container, isFilterActive) {
    const sortedFolders = Array.from(node.folders.values()).sort((a, b) => a.name.localeCompare(b.name));
    sortedFolders.forEach((folder) => {
      const descendantFiles = this.collectFolderFiles(folder, []);
      if (!descendantFiles.length) return;

      const wrap = document.createElement("div");
      wrap.className = "tree-folder";

      const row = document.createElement("div");
      row.className = "tree-row";
      row.oncontextmenu = (e) => {
        e.preventDefault();
        this.openContextMenu(e, folder.path);
      };

      const isCollapsed = !isFilterActive && !this.expandedDirs.has(folder.path);
      const caret = document.createElement("span");
      caret.className = "tree-caret";
      caret.textContent = isCollapsed ? "▸" : "▾";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      const selectedCount = descendantFiles.filter((f) => this.selected.has(f.path)).length;
      cb.checked = selectedCount > 0 && selectedCount === descendantFiles.length;
      cb.indeterminate = selectedCount > 0 && selectedCount < descendantFiles.length;
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => {
        descendantFiles.forEach((f) => {
          if (cb.checked) {
            const mode = f.est_tokens > 4000 ? "outline" : "full";
            this.selected.set(f.path, { path: f.path, mode, start: "", end: "", tokens: f.est_tokens });
          } else {
            this.selected.delete(f.path);
          }
        });
        this.renderFiles();
        this.renderChips();
      };

      const lbl = document.createElement("span");
      lbl.className = "tree-folder-lbl";
      lbl.textContent = `📁 ${folder.name}`;

      row.onclick = () => {
        if (this.expandedDirs.has(folder.path)) {
          this.expandedDirs.delete(folder.path);
        } else {
          this.expandedDirs.add(folder.path);
        }
        this.renderFiles();
      };

      row.append(caret, cb, lbl);
      wrap.appendChild(row);

      const childrenBox = document.createElement("div");
      childrenBox.className = `tree-children${isCollapsed ? " collapsed" : ""}`;
      this.renderTreeNode(folder, childrenBox, isFilterActive);
      wrap.appendChild(childrenBox);
      container.appendChild(wrap);
    });

    const sortedFiles = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
    sortedFiles.forEach((f) => {
      const row = document.createElement("div");
      row.className = "tree-row";
      row.title = "Double-click to view/edit · Right-click for options";
      row.oncontextmenu = (e) => {
        e.preventDefault();
        this.openContextMenu(e, f.path);
      };

      const spacer = document.createElement("span");
      spacer.className = "tree-caret";
      spacer.textContent = "";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = this.selected.has(f.path);
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => {
        if (cb.checked) {
          const defaultMode = f.est_tokens > 4000 ? "outline" : "full";
          this.selected.set(f.path, { path: f.path, mode: defaultMode, start: "", end: "", tokens: f.est_tokens });
        } else {
          this.selected.delete(f.path);
        }
        this.renderFiles();
        this.renderChips();
      };

      const name = document.createElement("span");
      name.className = "tree-file-lbl";
      name.textContent = f.name;

      const meta = document.createElement("span");
      meta.className = "fmeta";
      meta.textContent = `${f.lines !== undefined ? f.lines + ' lines · ' : ''}~${f.est_tokens}t`;

      row.onclick = () => {
        cb.checked = !cb.checked;
        cb.onchange();
      };
      row.ondblclick = (e) => {
        e.stopPropagation();
        this.openEditor(f.path);
      };

      row.append(spacer, cb, name, meta);
      container.appendChild(row);
    });
  },

  openContextMenu(e, relPath) {
    this.ctxPath = relPath;
    const menu = document.getElementById("contextMenu");
    const pasteItem = document.getElementById("ctxPasteItem");
    if (pasteItem) {
      pasteItem.style.display = this.clipboard.path ? "flex" : "none";
    }
    menu.style.left = `${e.pageX}px`;
    menu.style.top = `${e.pageY}px`;
    menu.classList.remove("hidden");

    const closeMenu = () => {
      menu.classList.add("hidden");
      document.removeEventListener("click", closeMenu);
    };
    setTimeout(() => document.addEventListener("click", closeMenu), 10);
  },

  async ctxRename() {
    const oldPath = this.ctxPath;
    const newPath = prompt("Enter new path/name:", oldPath);
    if (!newPath || newPath.trim() === oldPath) return;

    const res = await fetch("/api/file/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_path: oldPath, new_path: newPath.trim() }),
    });
    const data = await res.json();
    if (!res.ok) return this.toast(data.error || "Rename failed", "red");
    if (this.editingPath === oldPath) this.closeEditor();
    this.selected.delete(oldPath);
    await this.loadFiles();
    this.toast(`Renamed to ${newPath.trim()}`, "success");
  },

  async ctxDelete() {
    const target = this.ctxPath;
    if (!confirm(`Are you sure you want to delete ${target}?`)) return;

    const res = await fetch("/api/file/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: target }),
    });
    const data = await res.json();
    if (!res.ok) return this.toast(data.error || "Delete failed", "red");
    if (this.editingPath === target) this.closeEditor();
    this.selected.delete(target);
    await this.loadFiles();
    this.toast(`Deleted ${target}`, "success");
  },

  renderFiles() {
    const q = document.getElementById("filter").value.toLowerCase().trim();
    const list = document.getElementById("fileList");
    list.innerHTML = "";

    const filtered = q && !q.startsWith("@")
      ? this.files.filter((f) => f.path.toLowerCase().includes(q))
      : this.files;

    const tree = this.buildFileTree(filtered);
    this.renderTreeNode(tree, list, Boolean(q));
    this.updateStats();
  },

  handleFilterKey(e) {
    const filterInput = document.getElementById("filter");
    const val = filterInput.value;
    if (val.includes("@")) {
      const query = val.split("@").pop().toLowerCase();
      const matches = this.files.filter((f) => f.path.toLowerCase().includes(query)).slice(0, 6);
      if (matches.length > 0) {
        this.openChoiceModal({
          title: "Select File (@mention)",
          description: "Click a file to add it directly to context:",
          options: matches.map((m) => ({
            label: m.path,
            hint: `~${m.est_tokens}t`,
            onSelect: () => {
              const defaultMode = m.est_tokens > 4000 ? "outline" : "full";
              this.selected.set(m.path, { path: m.path, mode: defaultMode, start: "", end: "", tokens: m.est_tokens });
              filterInput.value = "";
              this.renderFiles();
              this.renderChips();
              this.toast(`Added ${m.path} to context`, "success");
            },
          })),
        });
      } else {
        this.closeChoiceModal();
      }
    } else {
      this.closeChoiceModal();
    }
  },

  renderChips() {
    const box = document.getElementById("selectedChips");
    box.innerHTML = "";
    this.selected.forEach((spec, path) => {
      const chip = document.createElement("div");
      chip.className = "chip";
      const lbl = document.createElement("span");
      lbl.textContent = spec.mode === "range" && (spec.start || spec.end)
        ? `${path}:${spec.start || 1}-${spec.end || "end"}`
        : path;

      const modeBtn = document.createElement("button");
      modeBtn.className = "btn ghost";
      modeBtn.style.padding = "2px 8px";
      modeBtn.style.fontSize = "11px";
      modeBtn.textContent = `${spec.mode} ▾`;
      modeBtn.onclick = () => {
        this.openChoiceModal({
          title: `Context Mode for ${path}`,
          description: "AI selects line ranges automatically, or pick an override:",
          options: [
            {
              label: "Full File",
              hint: `~${spec.tokens} tokens`,
              onSelect: () => {
                spec.mode = "full";
                this.renderChips();
                this.updateStats();
              },
            },
            {
              label: "Outline Only (Save ~80% Tokens)",
              hint: `~${Math.round(spec.tokens * 0.2)} tokens`,
              onSelect: () => {
                spec.mode = "outline";
                this.renderChips();
                this.updateStats();
              },
            },
            {
              label: "Specific Line Range",
              hint: "Set start & end lines",
              onSelect: () => {
                spec.mode = "range";
                this.renderChips();
                this.updateStats();
              },
            },
            {
              label: "Remove File",
              hint: "Exclude",
              onSelect: () => {
                this.selected.delete(path);
                this.renderFiles();
                this.renderChips();
              },
            },
          ],
        });
      };
      chip.append(lbl, modeBtn);

      if (spec.mode === "range") {
        const sIn = document.createElement("input");
        sIn.className = "rng";
        sIn.placeholder = "start";
        sIn.value = spec.start;
        sIn.oninput = () => (spec.start = parseInt(sIn.value) || "");
        const eIn = document.createElement("input");
        eIn.className = "rng";
        eIn.placeholder = "end";
        eIn.value = spec.end;
        eIn.oninput = () => (spec.end = parseInt(eIn.value) || "");
        chip.append(sIn, eIn);
      }
      box.appendChild(chip);
    });
  },

  updateStats() {
    document.getElementById("fileStats").textContent = `${this.selected.size} / ${this.files.length} files`;
    let total = 0;
    this.selected.forEach((s) => {
      total += s.mode === "outline" ? Math.round(s.tokens * 0.2) : s.tokens;
    });
    document.getElementById("selectedTokens").textContent = `~${total.toLocaleString()} tokens`;
  },

  collectAnswers() {
    const answers = [];
    this.pendingQuestions.forEach((q, idx) => {
      const checked = document.querySelector(`input[name="qa_${idx}"]:checked`);
      const custom = document.getElementById(`qa_custom_${idx}`).value.trim();
      const ans = custom || (checked ? checked.value : "");
      if (ans) answers.push({ question: q.question, answer: ans });
    });
    return answers;
  },

  renderPlan(planItems) {
    const panel = document.getElementById("planPanel");
    const box = document.getElementById("planOut");
    const meta = document.getElementById("planMeta");

    if (planItems && planItems.length) {
      this.planTasks = planItems;
    }

    if (!this.planTasks.length) {
      panel.classList.add("hidden");
      box.innerHTML = "";
      return;
    }

    panel.classList.remove("hidden");
    box.innerHTML = "";
    let totalCount = 0;
    let doneCount = 0;

    this.planTasks.forEach((item) => {
      totalCount += 1;
      if (item.done) doneCount += 1;

      const wrap = document.createElement("div");
      wrap.className = "plan-item";

      const mainRow = document.createElement("div");
      mainRow.className = "plan-main";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.disabled = true;
      cb.style.width = "auto";
      cb.checked = Boolean(item.done);
      const txt = document.createElement("span");
      txt.textContent = item.text;
      if (item.done) txt.className = "plan-done";
      mainRow.append(cb, txt);
      wrap.appendChild(mainRow);

      if (item.subtasks && item.subtasks.length) {
        const subBox = document.createElement("div");
        subBox.className = "plan-sublist";
        item.subtasks.forEach((sub) => {
          totalCount += 1;
          if (sub.done) doneCount += 1;
          const subRow = document.createElement("div");
          subRow.className = "plan-sub";
          const scb = document.createElement("input");
          scb.type = "checkbox";
          scb.disabled = true;
          scb.style.width = "auto";
          scb.checked = Boolean(sub.done);
          const stxt = document.createElement("span");
          stxt.textContent = sub.text;
          if (sub.done) stxt.className = "plan-done";
          subRow.append(scb, stxt);
          subBox.appendChild(subRow);
        });
        wrap.appendChild(subBox);
      }
      box.appendChild(wrap);
    });

    meta.textContent = `${doneCount}/${totalCount} completed`;
  },

  async buildPrompt(isAutoNext = false) {
    const task = document.getElementById("task").value.trim();
    if (!task) return;

    this.goalFinished = false;

    // AI chooses or updates files needed
    const include_tree = document.getElementById("includeTree").checked;
    const chunk_size = parseInt(document.getElementById("chunkSize").value) || 120000;
    const files = Array.from(this.selected.values());
    const answers = this.collectAnswers();

    const res = await fetch("/api/build_prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        files,
        include_tree,
        chunk_size,
        goal_step: this.goalStep,
        answers,
        applied_log: this.appliedLog,
        mode: this.mode,
      }),
    });
    let data;
    try {
      data = await res.json();
    } catch (e) {
      this.setLedState(false, "Invalid server response");
      return this.toast("Failed to build prompt: invalid response", "red");
    }
    if (!res.ok) {
      this.setLedState(false, data.error || "Prompt build failed");
      return this.toast(data.error || "Failed to build prompt", "red");
    }
    document.getElementById("generatedPrompt").value = data.full_prompt;
    document.getElementById("promptMeta").textContent =
      `${data.chars.toLocaleString()} chars (~${data.est_tokens.toLocaleString()} tokens) · ${data.mode}`;

    this.jsonParts = data.json_parts || [];
    this.renderModeWarning(data);
    this.toggleJsonButton();
    this.renderJsonParts();

    const btns = document.getElementById("copyButtons");
    btns.innerHTML = "";
    if (data.parts.length > 1) {
      data.parts.forEach((part, idx) => {
        const b = document.createElement("button");
        b.className = "btn ghost";
        b.textContent = `Part ${idx + 1}/${data.parts.length}`;
        b.onclick = async () => {
          this.lastCopiedPrompt = part;
          await navigator.clipboard.writeText(part);
          this.toast(`Part ${idx + 1}/${data.parts.length} copied`);
        };
        btns.appendChild(b);
      });
    }

    if (data.parts && data.parts.length > 0) {
      this.lastCopiedPrompt = data.parts[0];
      try {
        await navigator.clipboard.writeText(data.parts[0]);
        if (document.getElementById("autoUnselect").checked) {
          this.selected.clear();
          this.renderFiles();
          this.renderChips();
        }
      } catch (e) {
        // Fallback if window is blurred during auto-copy
      }

      const firstLabel = data.parts.length === 1 ? "Prompt" : `Part 1/${data.parts.length}`;
      this.showPopup({
        type: "red",
        title: `Step ${this.goalStep}: ${firstLabel} Copied to Clipboard`,
        message: "Paste into AI chat, copy the AI reply, then switch back here — it will auto-capture & run the next step!",
      });
    }

    this.setGoalBadge("WAITING REPLY", "active");
    this.setStage("paste");
  },

  toggleJsonPanel() {
    const panel = document.getElementById("jsonPartsPanel");
    if (!panel) return;
    panel.classList.toggle("hidden");
  },

  renderJsonParts() {
    const list = document.getElementById("jsonPartsList");
    if (!list) return;
    list.innerHTML = "";
    const parts = this.jsonParts || [];
    this.jsonParts.forEach((obj, idx) => {
      const row = document.createElement("div");
      row.className = "json-part-row";
      const meta = document.createElement("span");
      meta.className = "json-part-meta";
      meta.textContent = `Part ${obj.part}/${obj.total_parts} · ${obj.prompt.length.toLocaleString()} chars`;
      const btn = document.createElement("button");
      btn.className = "btn ghost";
      btn.textContent = `Copy part ${obj.part}`;
      btn.onclick = () => this.copyJsonPart(idx);
      row.append(meta, btn);
      list.appendChild(row);
    });
    const progress = document.getElementById("copyAllProgress");
    if (progress) progress.textContent = "";
  },

  async copyJsonPart(index) {
    const obj = (this.jsonParts || [])[index];
    if (!obj) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(obj));
      this.toast(`Copied JSON part ${obj.part}/${obj.total_parts}`, "success");
    } catch (e) {
      this.toast("Clipboard permission denied", "red");
    }
  },

  async copyAllJsonParts() {
    const parts = this.jsonParts || [];
    if (!parts.length) return;
    const btn = document.getElementById("copyAllJsonBtn");
    const progress = document.getElementById("copyAllProgress");
    if (btn) btn.disabled = true;
    try {
      for (let i = 0; i < parts.length; i++) {
        await navigator.clipboard.writeText(JSON.stringify(parts[i]));
        if (progress) progress.textContent = `Copied ${i + 1}/${parts.length}`;
        if (i < parts.length - 1) {
          await new Promise((resolve) => setTimeout(resolve, 400));
        }
      }
      this.toast(`Copied all ${parts.length} JSON part(s)`, "success");
    } catch (e) {
      this.toast("Clipboard permission denied", "red");
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  downloadJsonParts() {
    const parts = this.jsonParts || [];
    if (!parts.length) return;
    const blob = new Blob([JSON.stringify(parts, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chatbridge_prompt_${this.mode}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  async undoLast() {
    try {
      const res = await fetch("/api/undo", { method: "POST" });
      const data = await res.json();
      if (!res.ok) return this.toast(data.error || "Nothing to undo.", "red");
      this.lastFailedErrors = [];
      this.setLedState(true);
      await this.loadFiles();
      const undoModal = document.getElementById("undoModal");
      if (undoModal && !undoModal.classList.contains("hidden")) await this.openUndoModal();
      this.toast(data.message || "Last change undone.", "success");
    } catch (e) {
      this.toast("Undo failed: connection error", "red");
    }
  },

  async openUndoModal() {
    try {
      const res = await fetch("/api/undo/list");
      const data = await res.json();
      const box = document.getElementById("undoList");
      box.innerHTML = "";
      const batches = (data && data.batches) || [];
      if (!batches.length) {
        box.innerHTML = `<div class="muted" style="padding: 12px;">No undo batches available.</div>`;
      } else {
        batches.forEach((b) => {
          const row = document.createElement("div");
          row.className = "json-part-row";
          const meta = document.createElement("span");
          meta.className = "json-part-meta";
          meta.textContent = `${b.name} (${b.count} file(s))`;
          const btn = document.createElement("button");
          btn.className = "btn ghost";
          btn.textContent = "Undo";
          btn.onclick = async () => { await this.undoBatch(b.name); };
          row.append(meta, btn);
          box.appendChild(row);
        });
      }
      document.getElementById("undoModal").classList.remove("hidden");
    } catch (e) {
      this.toast("Failed to load undo history", "red");
    }
  },

  closeUndoModal() {
    const el = document.getElementById("undoModal");
    if (el) el.classList.add("hidden");
  },

  async undoBatch(name) {
    try {
      const res = await fetch("/api/undo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ batch: name }),
      });
      const data = await res.json();
      if (!res.ok) return this.toast(data.error || "Undo failed", "red");
      this.lastFailedErrors = [];
      this.setLedState(true);
      await this.loadFiles();
      const undoModal = document.getElementById("undoModal");
      if (undoModal && !undoModal.classList.contains("hidden")) await this.openUndoModal();
      else this.closeUndoModal();
      this.toast(data.message || "Undone", "success");
    } catch (e) {
      this.toast("Undo failed: connection error", "red");
    }
  },

  renderQuestions(questions) {
    this.pendingQuestions = questions || [];
    const box = document.getElementById("questionsOut");
    box.innerHTML = "";
    if (!this.pendingQuestions.length) return;

    this.pendingQuestions.forEach((q, idx) => {
      const card = document.createElement("div");
      card.className = "qa-card";
      const title = document.createElement("div");
      title.className = "qa-title";
      title.textContent = q.question;
      card.appendChild(title);

      (q.options || []).forEach((opt) => {
        const lbl = document.createElement("label");
        lbl.className = "qa-opt";
        const r = document.createElement("input");
        r.type = "radio";
        r.name = `qa_${idx}`;
        r.value = opt;
        r.onchange = () => {
          if (this.pendingQuestions.length === 1) {
            this.submitAnswersAndAdvance();
          }
        };
        lbl.append(r, document.createTextNode(opt));
        card.appendChild(lbl);
      });

      const custom = document.createElement("input");
      custom.id = `qa_custom_${idx}`;
      custom.placeholder = "Type custom answer & press Enter...";
      custom.onkeydown = (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          this.submitAnswersAndAdvance();
        }
      };
      card.appendChild(custom);
      box.appendChild(card);
    });
  },

  async submitAnswersAndAdvance() {
    this.goalStep += 1;
    await this.buildPrompt(true);
    document.getElementById("aiResponse").value = "";
    this.renderQuestions([]);
  },

  async tryAutoCaptureClipboard() {
    if (this.stage !== "paste" || this.goalFinished || this.isProcessing) return;
    try {
      const clipText = await navigator.clipboard.readText();
      const trimmed = (clipText || "").trim();
      if (!trimmed || trimmed === this.lastCopiedPrompt.trim()) return;
      if (trimmed === this.lastCapturedText) return; // no-replay guard
      if (trimmed.includes("OUTPUT FORMAT (REQUIRED")) return;

      const hasProtocolTag = /<<<(EDIT|WRITE|NEED_FILES|ASK|PLAN|STATUS)/.test(trimmed);
      if (!hasProtocolTag) return;

      const aiBox = document.getElementById("aiResponse");
      if (aiBox.value.trim() === trimmed) return;
       aiBox.value = clipText;
       this.lastCapturedText = trimmed;
       await this.runOps(true);
    } catch (e) {
      // Clipboard read requires focus/permission
    }
  },

  async runOps(apply) {
    if (this.isProcessing) return;
    this.isProcessing = true;
    try {
      const text = document.getElementById("aiResponse").value || "";

      let res, data;
      try {
        res = await fetch("/api/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, apply }),
        });
        data = await res.json();
      } catch (err) {
        this.setLedState(false, "Network or server error during patch apply.");
        this.toast("Connection error communicating with local server.", "red");
        return;
      }

      if (data.plan && data.plan.length) {
        this.renderPlan(data.plan);
      }

      const hasQuestions = (data.questions || []).length > 0;
      const hasNeedFiles = (data.need_files || []).length > 0;

      this.renderQuestions(data.questions || []);

      const out = document.getElementById("diffOut");
      out.innerHTML = "";
      const results = data.results || [];
      document.getElementById("applyMeta").textContent =
        `${results.length} edit(s) · ${(data.need_files || []).length} file req(s) · ${(data.questions || []).length} question(s)`;

      let appliedCount = 0;
      const failedItems = [];
      results.forEach((r) => {
        const isApplied = apply && r.status === "applied";
        const card = document.createElement("details");
        card.className = "diff-file";
        card.open = !isApplied;

        const hdr = document.createElement("summary");
        hdr.className = `diff-header ${r.status}`;
        hdr.style.cursor = "pointer";
        hdr.textContent = `${isApplied ? "▸" : "▾"} [${r.status.toUpperCase()}] ${r.path}`;
        card.ontoggle = () => {
          hdr.textContent = `${card.open ? "▾" : "▸"} [${r.status.toUpperCase()}] ${r.path}`;
        };
        card.appendChild(hdr);

        const body = document.createElement("div");
        body.className = "diff-body";
        if (r.message) {
          const line = document.createElement("div");
          line.className = "dline del";
          line.textContent = r.message;
          body.appendChild(line);
        } else if (r.diff_lines && r.diff_lines.length) {
          r.diff_lines.forEach((dl) => {
            const line = document.createElement("div");
            line.className = `dline ${dl.kind}`;
            line.textContent = dl.text;
            body.appendChild(line);
          });
        } else {
          const line = document.createElement("div");
          line.className = "dline meta";
          line.textContent = "(no text changes)";
          body.appendChild(line);
        }
        card.appendChild(body);
        out.appendChild(card);

        if (isApplied) {
          appliedCount += 1;
          this.appliedLog.push(`Step ${this.goalStep}: updated ${r.path}`);
        } else if (r.status === "error") {
          failedItems.push({ path: r.path, message: r.message || "Unknown error" });
        }
      });

      if (failedItems.length > 0) {
        this.lastFailedErrors = failedItems;
        this.setLedState(false, failedItems.map((f) => `${f.path}: ${f.message}`).join(" | "));
        this.showPopup({
          type: "red",
          title: `Self-Heal Triggered: ${failedItems.length} Patch Error(s)`,
          message: failedItems.map((f) => `• ${f.path}: ${f.message}`).join("\n") + "\n\nAuto-healing in 2.5s or click below to trigger immediately.",
          actions: [
            {
              label: "Heal & Retry Now",
              cls: "danger",
              onClick: () => this.selfHeal(),
            },
          ],
        });
        setTimeout(() => {
          if (this.lastFailedErrors.length > 0) this.selfHeal();
        }, 2500);
        return;
      } else {
        this.lastFailedErrors = [];
        this.setLedState(true);
      }

      if (apply) {
        await this.loadFiles();
      }

      // Handle AI-requested files automatically
      if (hasNeedFiles) {
        data.need_files.forEach((nf) => {
          const match = this.files.find((f) => f.path === nf.path);
          const existing = this.selected.get(nf.path);
          const requestedMode = nf.mode || (existing ? existing.mode : "full");
          this.selected.set(nf.path, {
            path: nf.path,
            mode: requestedMode,
            start: nf.start || "",
            end: nf.end || "",
            tokens: match ? match.est_tokens : 500,
          });
        });
        this.renderFiles();
        this.renderChips();

        if (!hasQuestions && (data.status_flag === "NEED_MORE" || !data.status_flag)) {
          this.goalStep += 1;
          await this.buildPrompt(true);
          document.getElementById("aiResponse").value = "";
          this.setGoalBadge("WAITING NEXT", "active");
          return;
        }
      }

      if (apply && data.status_flag === "APPLIED_NEXT" && !hasQuestions) {
        this.goalStep += 1;
        await this.buildPrompt(true);
        document.getElementById("aiResponse").value = "";
        this.setGoalBadge("WAITING NEXT", "active");
        this.showPopup({
          type: "red",
          title: `Step ${this.goalStep} Prompt Copied — Go to AI`,
          message: "Changes applied successfully! Next step prompt has been copied to your clipboard. Paste it into AI chat to continue.",
          actions: [
            {
              label: "Copy Prompt Again",
              cls: "ghost",
              onClick: async () => {
                if (this.lastCopiedPrompt) {
                  await navigator.clipboard.writeText(this.lastCopiedPrompt);
                  this.toast("Prompt copied again!", "success");
                }
              },
            },
          ],
        });
        return;
      }

      if (data.status_flag === "DONE") {
        this.goalFinished = true;
        this.selected.clear();
        this.renderFiles();
        this.renderChips();
        this.setGoalBadge("DONE", "done");
        this.setStage("paste");

        const completionMsg = "=== GOAL COMPLETED SUCCESSFULLY ===\nAll patches applied and verified by Gomaa Relay. The task is fully done.";
        this.lastCopiedPrompt = completionMsg;
        try {
          await navigator.clipboard.writeText(completionMsg);
        } catch (err) {}

        this.showPopup({
          type: "success",
          title: "Goal Finished & Copied to Clipboard!",
          message: "All edits successfully applied. A confirmation message has been copied to your clipboard — paste it back into AI chat to let it know the goal is complete!",
          actions: [
            {
              label: "Copy Done Message Again",
              cls: "ghost",
              onClick: async () => {
                try {
                  await navigator.clipboard.writeText(completionMsg);
                  this.toast("Done confirmation copied!", "success");
                } catch (e) {
                  this.toast("Clipboard permission denied", "red");
                }
              },
            },
            {
              label: "New Goal",
              cls: "success",
              onClick: () => this.resetGoal(),
            },
          ],
        });
      } else if (hasQuestions || data.status_flag === "NEED_MORE" || data.status_flag === "APPLIED_NEXT") {
        this.setGoalBadge("WAITING INPUT", "active");
        if (hasQuestions) {
          this.showPopup({
            type: "info",
            title: "Select an Option",
            message: "Click an option below to auto-advance to the next step.",
          });
        }
      }
    } finally {
      this.isProcessing = false;
    }
  },

  // Code Editor Modal & Colorful Syntax Highlighter
  async openEditor(relPath) {
    const res = await fetch(`/api/file?path=${encodeURIComponent(relPath)}`);
    const data = await res.json();
    if (!res.ok) return this.toast(data.error || "Failed to open file", "red");

    this.editingPath = relPath;
    document.getElementById("editorTitle").textContent = relPath;
    const ext = relPath.includes(".") ? relPath.split(".").pop().toUpperCase() : "TEXT";
    document.getElementById("editorLangBadge").textContent = ext;

    const area = document.getElementById("editorCode");
    area.value = data.content;
    this.syncEditorGutter();
    this.updateHighlighting();
    document.getElementById("editorModal").classList.remove("hidden");
    area.focus();
  },

  closeEditor() {
    document.getElementById("editorModal").classList.add("hidden");
    this.editingPath = "";
  },

  _highlightTimer: null,
  handleEditorInput() {
    this.syncEditorGutter();
    if (this._highlightTimer) clearTimeout(this._highlightTimer);
    this._highlightTimer = setTimeout(() => this.updateHighlighting(), 120);
  },

  syncEditorGutter() {
    const area = document.getElementById("editorCode");
    const gutter = document.getElementById("editorLines");
    const lineCount = (area.value.match(/\n/g) || []).length + 1;
    const nums = [];
    for (let i = 1; i <= lineCount; i++) nums.push(i);
    gutter.textContent = nums.join("\n");
  },

  syncEditorScroll() {
    const area = document.getElementById("editorCode");
    const gutter = document.getElementById("editorLines");
    const backdrop = document.getElementById("editorBackdrop");
    if (gutter) gutter.scrollTop = area.scrollTop;
    if (backdrop) {
      backdrop.scrollTop = area.scrollTop;
      backdrop.scrollLeft = area.scrollLeft;
    }
  },

  updateHighlighting() {
    const area = document.getElementById("editorCode");
    const backdrop = document.getElementById("editorBackdrop");
    if (!area || !backdrop) return;
    const text = area.value;
    const ext = (this.editingPath || "").split(".").pop().toLowerCase();
    backdrop.innerHTML = this.highlightCode(text, ext);
  },

  escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  },

  highlightCode(code, ext) {
    const safe = this.escapeHtml(code);
    if (ext === "py") {
      return safe
        .replace(/(#[^\n]*)/g, '<span class="syntax-com">$1</span>')
        .replace(/('(?:\\.|[^'])*'|"(?:\\.|[^"])*")/g, '<span class="syntax-str">$1</span>')
        .replace(/\b(def|class|import|from|return|if|else|elif|for|in|while|try|except|with|as|not|and|or|True|False|None|self|async|await)\b/g, '<span class="syntax-kw">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="syntax-num">$1</span>');
    } else if (["html", "xml"].includes(ext)) {
      return safe
        .replace(/(&lt;!--[\s\S]*?--&gt;)/g, '<span class="syntax-com">$1</span>')
        .replace(/('(?:\\.|[^'])*'|"(?:\\.|[^"])*")/g, '<span class="syntax-str">$1</span>')
        .replace(/(&lt;\/?[a-zA-Z0-9_-]+)/g, '<span class="syntax-tag">$1</span>')
        .replace(/(\s[a-zA-Z0-9_-]+)(?==)/g, '<span class="syntax-attr">$1</span>');
    } else if (["js", "ts", "json"].includes(ext)) {
      return safe
        .replace(/(\/\/[^\n]*|\/\*[\s\S]*?\*\/)/g, '<span class="syntax-com">$1</span>')
        .replace(/('(?:\\.|[^'])*'|"(?:\\.|[^"])*"|`(?:\\.|[^`])*`)/g, '<span class="syntax-str">$1</span>')
        .replace(/\b(const|let|var|function|return|if|else|for|in|while|import|export|default|async|await|class|new|typeof|try|catch)\b/g, '<span class="syntax-kw">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="syntax-num">$1</span>');
    } else if (["css", "scss"].includes(ext)) {
      return safe
        .replace(/(\/\*[\s\S]*?\*\/)/g, '<span class="syntax-com">$1</span>')
        .replace(/('(?:\\.|[^'])*'|"(?:\\.|[^"])*")/g, '<span class="syntax-str">$1</span>')
        .replace(/([a-zA-Z0-9_-]+)(?=\s*\{)/g, '<span class="syntax-tag">$1</span>')
        .replace(/([a-zA-Z0-9_-]+)(?=\s*:)/g, '<span class="syntax-attr">$1</span>')
        .replace(/\b(\d+(?:px|em|rem|%|vh|vw)?)\b/g, '<span class="syntax-num">$1</span>');
    } else if (["toml", "yaml", "yml"].includes(ext)) {
      return safe
        .replace(/(#.*)/g, '<span class="syntax-com">$1</span>')
        .replace(/('(?:\\.|[^'])*'|"(?:\\.|[^"])*")/g, '<span class="syntax-str">$1</span>')
        .replace(/^([a-zA-Z0-9_-]+)(?=\s*=)/gm, '<span class="syntax-attr">$1</span>')
        .replace(/^(\s*[a-zA-Z0-9_-]+)(?=\s*:)/gm, '<span class="syntax-attr">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="syntax-num">$1</span>');
    }
    return safe;
  },

  async saveEditor() {
    if (!this.editingPath) return;
    const content = document.getElementById("editorCode").value;
    const res = await fetch("/api/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: this.editingPath, content }),
    });
    const data = await res.json();
    if (!res.ok) return this.toast(data.error || "Save failed", "red");
    await this.loadFiles();
    this.toast(`Saved ${this.editingPath}`, "success");
    this.closeEditor();
  },

  init() {
    document.getElementById("task").value = "";
    document.getElementById("aiResponse").value = "";
    document.getElementById("generatedPrompt").value = "";
    this.setLedState(true);
    this.setStage("goal");
    this.initModePicker();

    // Auto-capture clipboard on window focus, visibility change, paste box focus, and background poll
    window.addEventListener("focus", () => this.tryAutoCaptureClipboard());
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") this.tryAutoCaptureClipboard();
    });
    setInterval(() => {
      if (document.hasFocus()) this.tryAutoCaptureClipboard();
    }, 900);

    const aiBox = document.getElementById("aiResponse");
    aiBox.addEventListener("focus", () => this.tryAutoCaptureClipboard());
     aiBox.addEventListener("paste", () => {
       setTimeout(() => {
         if (aiBox.value.trim()) this.runOps(true);
       }, 60);
     });

    const taskBox = document.getElementById("task");
    taskBox.addEventListener("keydown", async (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (taskBox.value.trim()) {
          await this.buildPrompt(false);
        }
      }
    });

    const autoSmart = document.getElementById("autoSelectSmart");
    let smartTimer = null;
    if (autoSmart) {
      taskBox.addEventListener("input", () => {
        if (!autoSmart.checked || this.goalFinished) return;
        if (taskBox.value.trim().length < 8) return;
        if (this.selected.size > 0) return;
        clearTimeout(smartTimer);
        smartTimer = setTimeout(() => {
          if (autoSmart.checked) this.smartSelect();
        }, 700);
      });
      autoSmart.addEventListener("change", () => {
        if (!autoSmart.checked) clearTimeout(smartTimer);
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      ["browserModal", "editorModal", "choiceModal", "errorModal", "optionsModal", "undoModal"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add("hidden");
      });
      const ctx = document.getElementById("contextMenu");
      if (ctx) ctx.classList.add("hidden");
    });

    const editorArea = document.getElementById("editorCode");
    editorArea.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        this.saveEditor();
      } else if (e.key === "Tab") {
        e.preventDefault();
        const start = editorArea.selectionStart;
        const end = editorArea.selectionEnd;
        const indent = this.editingPath.endsWith(".py") ? "    " : "  ";
        editorArea.value = editorArea.value.substring(0, start) + indent + editorArea.value.substring(end);
        editorArea.selectionStart = editorArea.selectionEnd = start + indent.length;
        this.syncEditorGutter();
      }
    });
  },
};

App.init();
App.loadFiles();