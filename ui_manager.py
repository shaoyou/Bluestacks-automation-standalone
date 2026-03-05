#!/usr/bin/env python3
import json
import queue
import signal
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Dict, Optional


BASE_DIR = Path(__file__).resolve().parent
PLANS_DIR = BASE_DIR / "plans"
BOT_PATH = BASE_DIR / "adb_bot.py"
DEFAULT_ADB = "adb"


TEMPLATE_PLAN = {
    "device": "127.0.0.1:5555",
    "jitter_px": 3,
    "max_runtime_sec": 0,
    "actions": [
        {"type": "click", "x": 1000, "y": 650},
        {"type": "wait", "seconds": 1.2},
        {
            "type": "loop",
            "count": -1,
            "actions": [
                {
                    "type": "patrol",
                    "from": {"x": 300, "y": 400},
                    "to": {"x": 900, "y": 400},
                    "duration_ms": 600,
                    "leg_wait_sec": 0.5,
                    "rounds": 1,
                },
                {"type": "wait", "seconds": 0.8, "jitter_seconds": 0.2},
            ],
        },
    ],
}


class RunnerSlot:
    def __init__(self, root: tk.Tk, frame: ttk.LabelFrame, slot_name: str, get_plan_path):
        self.root = root
        self.frame = frame
        self.slot_name = slot_name
        self.get_plan_path = get_plan_path
        self.proc: Optional[subprocess.Popen[str]] = None
        self.thread: Optional[threading.Thread] = None
        self.out_q: queue.Queue[str] = queue.Queue()

        self.script_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.adb_var = tk.StringVar(value=DEFAULT_ADB)

        ttk.Label(frame, text="Script").grid(row=0, column=0, sticky="w")
        self.script_combo = ttk.Combobox(frame, textvariable=self.script_var, state="readonly", width=28)
        self.script_combo.grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(frame, text="Device").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.device_var).grid(row=1, column=1, sticky="ew", padx=4)

        ttk.Label(frame, text="ADB").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.adb_var).grid(row=2, column=1, sticky="ew", padx=4)

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(btn_row, text=f"Start {slot_name}", command=self.start).pack(side="left")
        ttk.Button(btn_row, text=f"Stop {slot_name}", command=self.stop).pack(side="left", padx=6)

        self.log_text = tk.Text(frame, height=12, width=56)
        self.log_text.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=4)
        self.log_text.configure(state="disabled")

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

    def set_script_choices(self, names):
        self.script_combo["values"] = names
        if names and self.script_var.get() not in names:
            self.script_var.set(names[0])

    def append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def poll_queue(self):
        try:
            while True:
                line = self.out_q.get_nowait()
                self.append_log(line)
        except queue.Empty:
            pass
        self.root.after(120, self.poll_queue)

    def _reader(self):
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self.out_q.put(f"[{self.slot_name}] {line}")
        rc = self.proc.wait()
        self.out_q.put(f"[{self.slot_name}] process exit code: {rc}\n")
        self.proc = None

    def start(self):
        if self.proc is not None:
            messagebox.showwarning("Running", f"{self.slot_name} already running")
            return
        script_name = self.script_var.get().strip()
        if not script_name:
            messagebox.showerror("Missing Script", f"{self.slot_name} 未选择脚本")
            return
        plan_path = self.get_plan_path(script_name)
        if not plan_path.exists():
            messagebox.showerror("Missing File", f"脚本不存在: {plan_path}")
            return

        cmd = ["python3", str(BOT_PATH), "--plan", str(plan_path), "--adb", self.adb_var.get().strip() or DEFAULT_ADB]
        if self.device_var.get().strip():
            cmd.extend(["--device", self.device_var.get().strip()])

        self.append_log(f"[{self.slot_name}] start: {' '.join(cmd)}\n")
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=None,
        )
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def stop(self):
        if self.proc is None:
            self.append_log(f"[{self.slot_name}] not running\n")
            return
        self.append_log(f"[{self.slot_name}] stopping...\n")
        try:
            self.proc.send_signal(signal.SIGINT)
        except Exception:
            self.proc.terminate()


class ScriptManagerUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("BlueStacks Script Manager")
        self.current_file: Optional[Path] = None
        self.plan_files: Dict[str, Path] = {}

        container = ttk.Frame(root, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=2)
        container.columnconfigure(1, weight=3)
        container.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(container, text="Scripts")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        top_btns = ttk.Frame(left)
        top_btns.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(top_btns, text="Refresh", command=self.refresh_scripts).pack(side="left")
        ttk.Button(top_btns, text="New", command=self.new_script).pack(side="left", padx=4)
        ttk.Button(top_btns, text="Save", command=self.save_script).pack(side="left")

        self.script_list = tk.Listbox(left, height=16)
        self.script_list.grid(row=1, column=0, sticky="nsew")
        self.script_list.bind("<<ListboxSelect>>", self.on_select_script)

        self.editor = tk.Text(left, wrap="none")
        self.editor.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        left.rowconfigure(2, weight=2)

        right = ttk.Frame(container)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        slot_a_frame = ttk.LabelFrame(right, text="Runner A")
        slot_a_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        slot_b_frame = ttk.LabelFrame(right, text="Runner B")
        slot_b_frame.grid(row=1, column=0, sticky="nsew")

        self.slot_a = RunnerSlot(root, slot_a_frame, "A", self.get_plan_path)
        self.slot_b = RunnerSlot(root, slot_b_frame, "B", self.get_plan_path)
        self.slot_a.poll_queue()
        self.slot_b.poll_queue()

        self.refresh_scripts()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def get_plan_path(self, name: str) -> Path:
        return self.plan_files[name]

    def refresh_scripts(self):
        PLANS_DIR.mkdir(exist_ok=True)
        files = sorted(PLANS_DIR.glob("*.json"))
        self.plan_files = {f.name: f for f in files}
        names = list(self.plan_files.keys())

        self.script_list.delete(0, "end")
        for name in names:
            self.script_list.insert("end", name)

        self.slot_a.set_script_choices(names)
        self.slot_b.set_script_choices(names)

        if names and self.current_file is None:
            self.load_script(self.plan_files[names[0]])

    def new_script(self):
        name = simpledialog.askstring("New Script", "输入脚本名（如 farm_1.json）:")
        if not name:
            return
        if not name.endswith(".json"):
            name = f"{name}.json"
        path = PLANS_DIR / name
        if path.exists():
            messagebox.showerror("Exists", f"文件已存在: {name}")
            return
        path.write_text(json.dumps(TEMPLATE_PLAN, ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_scripts()
        self.load_script(path)

    def on_select_script(self, _event):
        idx = self.script_list.curselection()
        if not idx:
            return
        name = self.script_list.get(idx[0])
        self.load_script(self.plan_files[name])

    def load_script(self, path: Path):
        self.current_file = path
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", path.read_text(encoding="utf-8"))

    def save_script(self):
        if self.current_file is None:
            messagebox.showwarning("No File", "请先选择或新建脚本")
            return
        raw = self.editor.get("1.0", "end").strip()
        if not raw:
            messagebox.showerror("Invalid", "脚本内容为空")
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            messagebox.showerror("JSON Error", f"JSON 格式错误: {exc}")
            return
        self.current_file.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        messagebox.showinfo("Saved", f"已保存: {self.current_file.name}")
        self.refresh_scripts()

    def on_close(self):
        if self.slot_a.proc is not None or self.slot_b.proc is not None:
            if not messagebox.askyesno("Exit", "仍有脚本在运行，是否退出并停止进程？"):
                return
            self.slot_a.stop()
            self.slot_b.stop()
        self.root.destroy()


def main():
    PLANS_DIR.mkdir(exist_ok=True)
    root = tk.Tk()
    root.geometry("1360x860")
    app = ScriptManagerUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
