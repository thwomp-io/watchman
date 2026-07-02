# Installer branding assets (noir theme)

Windows installer art consumed by `tauri.conf.json bundle.windows` — the console's `noir` house
style (bg `#08090c` · ink `#f4f6fb` · amber `#ffd13d` · teal `#37e0d8`, Menlo/mono wordmark).

- `nsis-sidebar.{svg,bmp}` (164×314) — welcome/finish page left strip (image-only region → full noir)
- `nsis-header.{svg,bmp}` (150×57) — page header block (image-only region → full noir)
- `wix-banner.{svg,bmp}` (493×58) — MSI top band. ⚠ the installer draws BLACK title text over the
  LEFT of this bitmap → left stays light; noir block right, icon-only.
- `wix-dialog.{svg,bmp}` (493×312) — MSI welcome/finish background. ⚠ installer text lives on the
  RIGHT two-thirds → right stays light; noir art strip left.

SVG is the SOURCE (icon referenced from `../icons/128x128.png`); BMP is what WiX/NSIS consume
(24-bit, exact dimensions). Regenerate:

    rsvg-convert -o /tmp/x.png <name>.svg
    ffmpeg -y -i /tmp/x.png -frames:v 1 -update 1 -pix_fmt bgr24 <name>.bmp
