"""AutoGPT ChatGPT-account chat application."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from autogpt.auth import AuthError, ChatGPTAuth
from autogpt.client import list_models, stream_reply


class AutoGPTApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AutoGPT · ChatGPT account")
        self.geometry("900x700")
        self.minsize(680, 520)
        self.auth = ChatGPTAuth()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.models: list[tuple[str, str]] = []
        self.model_by_label: dict[str, str] = {}
        self.chat_history: list[dict[str, str]] = []
        self._build_ui()
        if self.auth.connected:
            self._show_chat()
            self._load_models()
        else:
            self._show_login()
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        self.root = ttk.Frame(self, padding=18)
        self.root.pack(fill="both", expand=True)

        self.login_view = ttk.Frame(self.root)
        ttk.Label(self.login_view, text="AutoGPT", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        ttk.Label(self.login_view, text="Sign in with your ChatGPT account", font=("Segoe UI", 11)).pack(anchor="w", pady=(4, 18))
        self.login_status = tk.StringVar(value="Not signed in")
        ttk.Label(self.login_view, textvariable=self.login_status, wraplength=600).pack(anchor="w", pady=(0, 18))
        self.login_button = ttk.Button(self.login_view, text="Continue with ChatGPT", command=self._sign_in)
        self.login_button.pack(anchor="w")

        self.chat_view = ttk.Frame(self.root)
        header = ttk.Frame(self.chat_view)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="AutoGPT", font=("Segoe UI", 20, "bold")).pack(side="left")
        self.account_var = tk.StringVar()
        ttk.Label(header, textvariable=self.account_var).pack(side="left", padx=14)
        ttk.Button(header, text="Sign out", command=self._sign_out).pack(side="right")

        model_row = ttk.Frame(self.chat_view)
        model_row.pack(fill="x", pady=(0, 10))
        ttk.Label(model_row, text="Model").pack(side="left")
        self.model_var = tk.StringVar(value="gpt-5.6-luna")
        self.model_box = ttk.Combobox(model_row, textvariable=self.model_var, state="normal", width=42)
        self.model_box.pack(side="left", padx=8)
        ttk.Button(model_row, text="Refresh", command=self._load_models).pack(side="left")

        self.output = tk.Text(self.chat_view, wrap="word", state="disabled", font=("Segoe UI", 10), padx=12, pady=10)
        self.output.pack(fill="both", expand=True)
        self.output.tag_configure("user", foreground="#2457a6", spacing1=8)
        self.output.tag_configure("assistant", foreground="#202124", spacing1=8)
        self.output.tag_configure("status", foreground="#70757a", spacing1=8)

        compose = ttk.Frame(self.chat_view)
        compose.pack(fill="x", pady=(10, 0))
        self.prompt = tk.Text(compose, height=4, wrap="word", font=("Segoe UI", 10))
        self.prompt.pack(side="left", fill="x", expand=True)
        self.send_button = ttk.Button(compose, text="Send", command=self._send)
        self.send_button.pack(side="left", padx=(10, 0), fill="y")
        self.prompt.bind("<Control-Return>", self._send_shortcut)

    def _show_login(self) -> None:
        self.chat_view.pack_forget()
        self.login_view.pack(fill="both", expand=True)

    def _show_chat(self) -> None:
        self.login_view.pack_forget()
        self.chat_view.pack(fill="both", expand=True)
        self.account_var.set(self.auth.email or self.auth.metadata.get("name") or "ChatGPT account")

    def _sign_in(self) -> None:
        self.login_button.configure(state="disabled")
        self.login_status.set("Opening your browser for ChatGPT authorization…")

        def work() -> None:
            try:
                label = self.auth.sign_in()
                self.events.put(("signed_in", label))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("auth_idle", None))

        threading.Thread(target=work, daemon=True).start()

    def _load_models(self) -> None:
        self._set_busy(True)

        def work() -> None:
            try:
                self.events.put(("models", list_models(self.auth)))
            except Exception as exc:
                self.events.put(("model_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _send(self) -> None:
        prompt = self.prompt.get("1.0", "end-1c").strip()
        if not prompt:
            return
        model_label = self.model_var.get().strip()
        model = self.model_by_label.get(model_label, model_label)
        if not model:
            messagebox.showinfo("Choose a model", "Select an available model or enter its model ID.")
            return

        self.prompt.delete("1.0", "end")
        self._append(f"You\n{prompt}", "user")
        self._append("AutoGPT\n", "assistant")
        self.chat_history.append({"role": "user", "content": prompt})
        self._set_busy(True)

        def on_text(delta: str) -> None:
            self.events.put(("delta", delta))

        def work() -> None:
            try:
                answer = stream_reply(self.auth, model, list(self.chat_history), on_text)
                self.events.put(("reply_done", answer))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _send_shortcut(self, _event: tk.Event) -> str:
        self._send()
        return "break"

    def _sign_out(self) -> None:
        self.auth.sign_out()
        self.chat_history.clear()
        self._append("Signed out.", "status")
        self.login_status.set("Not signed in")
        self._show_login()

    def _set_busy(self, busy: bool) -> None:
        self.send_button.configure(state="disabled" if busy else "normal")
        self.login_button.configure(state="disabled" if busy else "normal")

    def _append(self, text: str, tag: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text + "\n\n", tag)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _append_delta(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text, "assistant")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "signed_in":
                self._show_chat()
                self._append(f"Signed in as {payload}. Loading available models…", "status")
                self._load_models()
            elif kind == "models":
                self.models = payload  # type: ignore[assignment]
                self.model_by_label = {f"{display} ({slug})": slug for slug, display in self.models}
                self.model_box["values"] = list(self.model_by_label)
                if self.models:
                    first_label = next(iter(self.model_by_label))
                    self.model_var.set(first_label)
                self._append(f"Loaded {len(self.models)} available model(s).", "status")
                self._set_busy(False)
            elif kind == "model_error":
                self._append(f"Could not load models: {payload}. You can type a model ID manually.", "status")
                self._set_busy(False)
            elif kind == "delta":
                self._append_delta(str(payload))
            elif kind == "reply_done":
                self.chat_history.append({"role": "assistant", "content": str(payload)})
                self._append("\n", "assistant")
                self._set_busy(False)
            elif kind == "auth_idle":
                self.login_button.configure(state="normal")
            elif kind == "error":
                if self.login_view.winfo_manager():
                    self.login_status.set(str(payload))
                else:
                    self._append(f"Error: {payload}", "status")
                self._set_busy(False)
        self.after(100, self._drain_events)


if __name__ == "__main__":
    AutoGPTApp().mainloop()

