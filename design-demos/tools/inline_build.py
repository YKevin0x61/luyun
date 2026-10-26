import re, sys, pathlib
for n in (1,2,3):
    d = pathlib.Path(f"dir-{n}")
    html = (d/"index.html").read_text(encoding="utf-8")
    html = re.sub(r'<link[^>]*href="tokens\.css"[^>]*>', lambda m: "<style>\n"+(d/"tokens.css").read_text(encoding="utf-8")+"\n</style>", html)
    html = re.sub(r'<script src="data\.js"></script>', lambda m: "<script>\n"+(d/"data.js").read_text(encoding="utf-8")+"\n</script>", html)
    def inline_jsx(m):
        src = m.group(1)
        return '<script type="text/babel">\n'+(d/src).read_text(encoding="utf-8")+"\n</script>"
    html = re.sub(r'<script type="text/babel"[^>]*src="([^"]+\.jsx)"[^>]*></script>', inline_jsx, html)
    out = pathlib.Path(f"_inline/dir-{n}.html")
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print("built", out, len(html))
