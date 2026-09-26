"""Schedule one-time SMS and WeChat messages on macOS."""
import subprocess
import queue
import threading
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

import runner
import scheduler
import store
import wechat

REFRESH_MS = 30_000


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SMS & WeChat Scheduler")
        self.minsize(780, 640)
        self._build_form()
        self._build_list()
        ttk.Label(
            self, foreground="gray", wraplength=740,
            text="SMS: stay logged in and keep Messages signed in. WeChat: keep the Mac awake and unlocked, "
                 "WeChat signed in, and its window unobstructed with the chosen chat visible in the sidebar. Avoid using WeChat at send time. "
                 "Messages more than 1 hour late are skipped.",
        ).pack(fill="x", padx=12, pady=(0, 10))
        self.refresh()

    # ---- form -------------------------------------------------------------
    def _build_form(self):
        f = ttk.LabelFrame(self, text="New message", padding=10)
        f.pack(fill="x", padx=12, pady=10)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Send via:").grid(row=0, column=0, sticky="w")
        self.channel = ttk.Combobox(f, values=["SMS", "WeChat"], state="readonly", width=16)
        self.channel.set("SMS")
        self.channel.grid(row=0, column=1, sticky="w", pady=2)
        self.channel.bind("<<ComboboxSelected>>", self._channel_changed)

        self.to_label = ttk.Label(f, text="To (phone):")
        self.to_label.grid(row=1, column=0, sticky="w")
        recipients = ttk.Frame(f)
        recipients.grid(row=1, column=1, sticky="ew", pady=2)
        recipients.columnconfigure(0, weight=1)
        self.phone = ttk.Entry(recipients)
        self.phone.grid(row=0, column=0, sticky="ew")
        self.thread = ttk.Combobox(recipients, state="readonly")
        self.thread.grid(row=0, column=0, sticky="ew")
        self.thread.grid_remove()
        self.load_button = ttk.Button(recipients, text="Load WeChat chats", command=self.load_threads)
        self.load_button.grid(row=0, column=1, padx=(6, 0))
        self.load_button.grid_remove()
        self.thread_hint = ttk.Label(f, foreground="gray", wraplength=620,
            text="Use unique WeChat remark names. The picker lists available sidebar chats; "
                 "open or pin the chat in WeChat first, then load chats. Run Check access before scheduling.")
        self.thread_hint.grid(row=2, column=1, sticky="w", pady=2)
        self.thread_hint.grid_remove()

        ttk.Label(f, text="Message:").grid(row=4, column=0, sticky="nw")
        self.msg = tk.Text(f, height=4, wrap="word")
        self.msg.grid(row=4, column=1, sticky="ew", pady=2)
        self.count = ttk.Label(f, text="0 chars", foreground="gray")
        self.count.grid(row=5, column=1, sticky="e")
        self.msg.bind("<KeyRelease>", lambda e: self.count.config(text=f"{len(self._message())} chars"))

        ttk.Label(f, text="When:").grid(row=6, column=0, sticky="w")
        w = ttk.Frame(f)
        w.grid(row=6, column=1, sticky="w", pady=2)
        d = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
        self.year = self._spin(w, d.year, d.year + 5, d.year, 6)
        ttk.Label(w, text="-").pack(side="left")
        self.month = self._spin(w, 1, 12, d.month, 3, "%02.0f")
        ttk.Label(w, text="-").pack(side="left")
        self.day = self._spin(w, 1, 31, d.day, 3, "%02.0f")
        ttk.Label(w, text="   ").pack(side="left")
        self.hour = self._spin(w, 1, 12, d.hour % 12 or 12, 3)
        ttk.Label(w, text=":").pack(side="left")
        self.minute = self._spin(w, 0, 59, d.minute, 3, "%02.0f")
        self.ampm = ttk.Combobox(w, values=["AM", "PM"], width=4, state="readonly")
        self.ampm.set("PM" if d.hour >= 12 else "AM")
        self.ampm.pack(side="left", padx=4)

        b = ttk.Frame(f)
        b.grid(row=7, column=1, sticky="e", pady=(6, 0))
        self.check_button = ttk.Button(b, text="Check access (no send)", command=self.check_access)
        self.check_button.pack(side="left", padx=4)
        ttk.Button(b, text="Schedule", command=self.schedule).pack(side="left")

    def _spin(self, parent, lo, hi, val, width, fmt=None):
        s = ttk.Spinbox(parent, from_=lo, to=hi, width=width, wrap=True, **({"format": fmt} if fmt else {}))
        s.set(f"{val:02d}" if fmt else val)
        s.pack(side="left")
        return s

    def _message(self):
        return self.msg.get("1.0", "end-1c").strip()

    def _when(self):
        h = int(self.hour.get()) % 12 + (12 if self.ampm.get() == "PM" else 0)
        return datetime(int(self.year.get()), int(self.month.get()), int(self.day.get()), h, int(self.minute.get()))

    # ---- list -------------------------------------------------------------
    def _build_list(self):
        f = ttk.Frame(self, padding=(12, 0))
        f.pack(fill="both", expand=True)
        cols = ("when", "channel", "to", "message", "status")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, title, width in [("when", "Time", 185), ("channel", "Via", 70), ("to", "To", 150), ("message", "Message", 250), ("status", "Status", 90)]:
            self.tree.heading(c, text=title)
            self.tree.column(c, width=width, stretch=(c == "message"))
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.show_details())

        b = ttk.Frame(self, padding=(12, 6))
        b.pack(fill="x")
        ttk.Button(b, text="Cancel selected", command=self.cancel).pack(side="left")
        ttk.Button(b, text="Remove from list", command=self.remove).pack(side="left", padx=4)
        ttk.Button(b, text="Details", command=self.show_details).pack(side="left")
        ttk.Button(b, text="Open log", command=lambda: subprocess.run(["open", "-t", str(scheduler.LOG)])).pack(side="right")
        ttk.Button(b, text="Refresh", command=self.refresh).pack(side="right", padx=4)

    def refresh(self):
        if getattr(self, "_after", None):
            self.after_cancel(self._after)
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for j in sorted(store.load(), key=lambda j: j["when"]):
            when = datetime.fromisoformat(j["when"]).strftime("%a %b %d %Y %I:%M %p")
            text = j["message"].replace("\n", " ")
            self.tree.insert("", "end", iid=j["id"], values=(when, "WeChat" if scheduler.channel(j) == "wechat" else "SMS", scheduler.recipient(j), text[:60], j["status"]))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel)
        self._after = self.after(REFRESH_MS, self.refresh)

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("SMS Scheduler", "Select a message first.")
            return None
        return next((j for j in store.load() if j["id"] == sel[0]), None)

    # ---- actions ----------------------------------------------------------
    def schedule(self):
        kind = "wechat" if self.channel.get() == "WeChat" else "sms"
        target = self.thread.get() if kind == "wechat" else scheduler.normalize_phone(self.phone.get())
        message = self._message()
        try:
            when = self._when()
        except ValueError:
            return messagebox.showerror("Invalid time", "That date doesn't exist.")
        if not target:
            return messagebox.showerror("Invalid recipient", "Load and select a WeChat chat." if kind == "wechat"
                                        else "Enter a 10-digit US number or a +country number.")
        if not message:
            return messagebox.showerror("No message", "Type a message to send.")
        if when <= datetime.now():
            return messagebox.showerror("Invalid time", "Pick a time in the future.")
        job = {
            "id": uuid.uuid4().hex[:8], "channel": kind, "message": message,
            "when": when.isoformat(timespec="minutes"), "status": "pending", "result": "",
            "created": datetime.now().isoformat(timespec="seconds"), "finished": None,
        }
        job["thread" if kind == "wechat" else "phone"] = target
        with store.locked() as jobs:
            jobs.append(job)
        try:
            scheduler.install_agent(job)
        except RuntimeError as e:
            with store.locked() as jobs:
                jobs.remove(store.find(jobs, job["id"]))
            return messagebox.showerror("Couldn't schedule", str(e))
        scheduler.log(f"{job['id']} scheduled for {job['when']} via {kind} to {target}")
        self.msg.delete("1.0", "end")
        self.count.config(text="0 chars")
        self.refresh()
        messagebox.showinfo("Scheduled", f"Scheduled via {self.channel.get()} to {target} at {when:%a %b %d %Y %I:%M %p}.")

    def cancel(self):
        job = self._selected()
        if not job:
            return
        if job["status"] != "pending":
            return messagebox.showinfo("SMS Scheduler", f"This message is already {job['status']}.")
        if not messagebox.askyesno("Cancel message", f"Cancel the message to {scheduler.recipient(job)}?"):
            return
        with store.locked() as jobs:
            j = store.find(jobs, job["id"])
            ok = j and j["status"] == "pending"
            if ok:
                j.update(status="cancelled", result="cancelled by user",
                         finished=datetime.now().isoformat(timespec="seconds"))
        if ok:
            scheduler.remove_agent(job["id"])
            scheduler.log(f"{job['id']} cancelled")
        else:
            messagebox.showinfo("SMS Scheduler", "Too late: it's already being sent.")
        self.refresh()

    def remove(self):
        job = self._selected()
        if not job:
            return
        if job["status"] in ("pending", "sending"):
            return messagebox.showinfo("SMS Scheduler", "Cancel it first; only finished messages can be removed.")
        with store.locked() as jobs:
            jobs.remove(store.find(jobs, job["id"]))
        self.refresh()

    def show_details(self):
        job = self._selected()
        if job:
            messagebox.showinfo("Message details", "\n".join([
                f"Via: {scheduler.channel(job)}", f"To: {scheduler.recipient(job)}", f"When: {job['when']}", f"Status: {job['status']}",
                f"Result: {job['result'] or '-'}", "", job["message"],
            ]))

    def _channel_changed(self, event=None):
        is_wechat = self.channel.get() == "WeChat"
        self.to_label.config(text="WeChat chat:" if is_wechat else "To (phone):")
        if is_wechat:
            self.phone.grid_remove()
            self.thread.grid()
            self.load_button.grid()
            self.thread_hint.grid()
        else:
            self.phone.grid()
            self.thread.grid_remove()
            self.load_button.grid_remove()
            self.thread_hint.grid_remove()

    def load_threads(self):
        self.load_button.config(state="disabled", text="Loading…")
        results = queue.Queue()
        def work():
            try:
                results.put((wechat.threads(), None))
            except Exception as exc:
                results.put((None, str(exc)))
        def poll():
            try:
                names, error = results.get_nowait()
            except queue.Empty:
                self.after(100, poll)
                return
            self.load_button.config(state="normal", text="Load WeChat chats")
            if error:
                return messagebox.showerror("WeChat access", error + "\n\nAllow Accessibility for the app launching "
                    "this scheduler, and Automation → System Events when macOS prompts you.")
            previous = self.thread.get()
            self.thread.config(values=names)
            self.thread.set(previous if previous in names else "")
            if not names:
                messagebox.showinfo("WeChat chats", "No unambiguous sidebar chats found. Open a conversation in WeChat, "
                                       "give it a unique remark name, then load again.")
        threading.Thread(target=work, daemon=True).start()
        self.after(100, poll)

    def check_access(self):
        kind = "wechat" if self.channel.get() == "WeChat" else "sms"
        target = self.thread.get() if kind == "wechat" else scheduler.normalize_phone(self.phone.get())
        if not target:
            return messagebox.showerror("Invalid recipient", "Choose a WeChat chat or enter an SMS number first.")
        path = runner.WECHAT_CHECK_RESULT if kind == "wechat" else runner.CHECK_RESULT
        path.unlink(missing_ok=True)
        try:
            if kind == "wechat":
                scheduler.install_wechat_check(target)
            else:
                scheduler.install_check(target)
        except RuntimeError as exc:
            return messagebox.showerror("Check failed", str(exc))
        self.check_button.config(state="disabled")
        self._poll_check(datetime.now() + timedelta(seconds=120), path, kind)

    def _poll_check(self, deadline, path, kind):
        if path.exists():
            self.check_button.config(state="normal")
            messagebox.showinfo(f"{kind} access", path.read_text().strip()
                                + "\n\nNothing was sent; this was a dry run.")
        elif datetime.now() > deadline:
            self.check_button.config(state="normal")
            messagebox.showwarning(f"{kind} access", "No result after 2 minutes. Look for a macOS "
                                   "permission prompt and click OK/Allow, then try again.")
        else:
            self.after(1000, self._poll_check, deadline, path, kind)


if __name__ == "__main__":
    App().mainloop()
