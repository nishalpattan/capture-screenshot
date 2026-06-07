# Screenshot Dependencies

The skill does not bundle third-party dependencies and does not install anything automatically. Use these notes only after a capture command reports a missing prerequisite.

## macOS

- Uses built-in `screencapture` for capture.
- Named and active-window capture compile `scripts/find_macos_window_id.m` with `clang` and the ApplicationServices framework.
- If `clang` is missing, install Apple Command Line Tools outside the skill workflow.
- Screen Recording permission is shown as `Screen & System Audio Recording` on newer macOS versions.
- Permission is per launching app:
  - Codex Desktop runs need `Codex` enabled.
  - CLI runs need the launching terminal enabled, such as `Terminal`, `iTerm`, or `VS Code`.
  - Using both Desktop and CLI may require both permissions.
- After changing the toggle, macOS may require quitting and reopening the host app.
- Named-window capture works for background/occluded windows (`screencapture -l` grabs the
  window's own content). Minimized windows and windows on other Spaces have no capturable
  bitmap; the skill reports them as `window_not_capturable` (exit 75) and asks you to restore
  the window rather than capturing the wrong thing.

## Windows 11+

- Uses PowerShell with .NET `System.Windows.Forms`, `System.Drawing`, and Win32 API calls.
- Use `powershell.exe -NoProfile -Sta` or `pwsh -Sta` when clipboard output is requested.
- No external modules are required.
- Named-window capture uses `PrintWindow` with `PW_RENDERFULLCONTENT`, so background/occluded
  windows capture their own content without being raised. Some GPU/DirectX/Electron windows
  (e.g. Chrome) can render black; the skill warns on stderr and still saves the image.
- Minimized windows are reported as `window_not_capturable` (exit 75), not restored.
- Windows privacy settings or enterprise policy may block screen capture.

## Linux

Linux support is best-effort because screenshot permissions differ across X11, Wayland, desktop environments, and compositors.

Common tools the helper can detect:

- GNOME: `gnome-screenshot`
- KDE: `spectacle`
- Wayland wlroots: `grim` plus `wl-copy` for clipboard
- X11 fallback: `scrot`, ImageMagick `import`
- X11 named-window lookup: `xdotool` plus ImageMagick `import`
- X11 window-state check (minimized vs viewable): `xprop` or `xwininfo` (optional; without them
  the skill cannot detect a minimized window and will attempt the capture anyway)
- Clipboard fallback: `xclip` or `xsel`

X11 occluded/background named-window capture requires a running **compositing manager** (so
windows keep an off-screen backing pixmap). Without a compositor, the occluded regions of a
background window may render black. Minimized (iconic) X11 windows are unmapped and cannot be
captured; the skill reports `window_not_capturable` (exit 75) and asks you to restore them.

Ubuntu package names commonly include `gnome-screenshot`, `grim`, `wl-clipboard`, `spectacle`, `scrot`, `imagemagick`, `xdotool`, `xclip`, `xsel`, and `x11-utils` (for `xprop`/`xwininfo`).

Arch Linux package names commonly include `gnome-screenshot`, `grim`, `wl-clipboard`, `spectacle`, `scrot`, `imagemagick`, `xdotool`, `xclip`, `xsel`, `xorg-xprop`, and `xorg-xwininfo`.

Fedora package names commonly include `gnome-screenshot`, `grim`, `wl-clipboard`, `spectacle`, `scrot`, `ImageMagick`, `xdotool`, `xclip`, `xsel`, `xprop`, and `xwininfo`.

Wayland named-window capture often cannot be done safely from a generic helper. The skill should fail closed instead of attempting compositor-specific bypasses.
