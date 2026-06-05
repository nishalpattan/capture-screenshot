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

## Windows 11+

- Uses PowerShell with .NET `System.Windows.Forms`, `System.Drawing`, and Win32 API calls.
- Use `powershell.exe -NoProfile -Sta` or `pwsh -Sta` when clipboard output is requested.
- No external modules are required.
- Windows privacy settings or enterprise policy may block screen capture.

## Linux

Linux support is best-effort because screenshot permissions differ across X11, Wayland, desktop environments, and compositors.

Common tools the helper can detect:

- GNOME: `gnome-screenshot`
- KDE: `spectacle`
- Wayland wlroots: `grim` plus `wl-copy` for clipboard
- X11 fallback: `scrot`, ImageMagick `import`
- X11 named-window lookup: `xdotool` plus ImageMagick `import`
- Clipboard fallback: `xclip` or `xsel`

Ubuntu package names commonly include `gnome-screenshot`, `grim`, `wl-clipboard`, `spectacle`, `scrot`, `imagemagick`, `xdotool`, `xclip`, and `xsel`.

Arch Linux package names commonly include `gnome-screenshot`, `grim`, `wl-clipboard`, `spectacle`, `scrot`, `imagemagick`, `xdotool`, `xclip`, and `xsel`.

Fedora package names commonly include `gnome-screenshot`, `grim`, `wl-clipboard`, `spectacle`, `scrot`, `ImageMagick`, `xdotool`, `xclip`, and `xsel`.

Wayland named-window capture often cannot be done safely from a generic helper. The skill should fail closed instead of attempting compositor-specific bypasses.
