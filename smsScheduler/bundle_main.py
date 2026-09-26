"""Single bundle executable: GUI by default, headless for launchd jobs."""
import sys
from pathlib import Path


def main():
    args = sys.argv[1:]
    if args and args[0] == "--runner":
        import runner
        runner.main(args[1:])
    elif args:
        raise SystemExit("Unknown arguments: " + " ".join(args))
    else:
        import app
        executable = Path(sys.executable).resolve()
        if "AppTranslocation" in executable.parts or str(executable).startswith("/Volumes/"):
            from tkinter import messagebox
            root = app.tk.Tk()
            root.withdraw()
            messagebox.showinfo("Install SMS & WeChat Scheduler",
                                "Drag SMS & WeChat Scheduler to Applications, eject the disk image, "
                                "and open the app from Applications before scheduling messages.")
            root.destroy()
            return
        app.App().mainloop()


if __name__ == "__main__":
    main()
