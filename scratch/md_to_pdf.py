import os
import subprocess
import time
import sys

# Paths
WORKSPACE_DIR = r"d:\Deepseek\camstar\CamstarModelingMCP"
MD_FILE = os.path.join(WORKSPACE_DIR, "docs", "mcp_execution_flow.md")
TEMP_HTML_BODY = os.path.join(WORKSPACE_DIR, "docs", "mcp_execution_flow_raw.html")
FINAL_HTML = os.path.join(WORKSPACE_DIR, "docs", "mcp_execution_flow.html")
PDF_FILE = os.path.join(WORKSPACE_DIR, "docs", "mcp_execution_flow.pdf")

# Beautiful, modern stylesheet for premium documentation look
CSS_STYLING = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1e293b;
    line-height: 1.6;
    max-width: 900px;
    margin: 0 auto;
    padding: 2.5rem;
    font-size: 15px;
}

h1, h2, h3, h4, h5, h6 {
    color: #0f172a;
    font-weight: 600;
    margin-top: 1.8rem;
    margin-bottom: 0.8rem;
}

h1 {
    font-size: 2.2rem;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 0.6rem;
    margin-top: 0;
}

h2 {
    font-size: 1.5rem;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 0.4rem;
    margin-top: 2rem;
}

h3 {
    font-size: 1.2rem;
    margin-top: 1.5rem;
}

code {
    font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
    font-size: 0.85em;
    background-color: #f1f5f9;
    padding: 0.2em 0.4em;
    border-radius: 4px;
    color: #0f172a;
}

pre {
    background-color: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 1.2rem;
    overflow-x: auto;
    margin: 1.5rem 0;
}

pre code {
    background-color: transparent;
    padding: 0;
    border-radius: 0;
    font-size: 0.82em;
    color: inherit;
    line-height: 1.5;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 2rem 0;
    font-size: 0.9em;
}

th, td {
    border: 1px solid #e2e8f0;
    padding: 0.8rem 1rem;
    text-align: left;
}

th {
    background-color: #f8fafc;
    color: #0f172a;
    font-weight: 600;
}

tr:nth-child(even) {
    background-color: #fafaf9;
}

blockquote {
    border-left: 4px solid #3b82f6;
    background-color: #eff6ff;
    color: #1e3a8a;
    padding: 0.8rem 1.2rem;
    margin: 1.5rem 0;
    border-radius: 0 8px 8px 0;
}

.mermaid {
    display: flex;
    justify-content: center;
    background-color: #fff;
    padding: 1.5rem;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin: 2rem 0;
}

hr {
    border: 0;
    border-top: 1px solid #e2e8f0;
    margin: 2.5rem 0;
}

/* Print Specific Rules */
@page {
    size: A4;
    margin: 2cm;
}

@media print {
    body {
        padding: 0;
        max-width: 100%;
    }
    
    pre, blockquote, table, img, .mermaid {
        page-break-inside: avoid;
    }
    
    h1, h2, h3 {
        page-break-after: avoid;
    }
}
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Camstar Modeling MCP Agent 调用全过程解析</title>
    <style>
        {css_styling}
    </style>
    <!-- Load Mermaid.js -->
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            // Convert standard markdown code blocks containing mermaid code into div.mermaid
            const blocks = document.querySelectorAll('pre.mermaid, pre code.language-mermaid');
            blocks.forEach(block => {
                const codeElement = block.tagName === 'CODE' ? block : block.querySelector('code');
                const container = block.tagName === 'PRE' ? block : block.parentElement;
                const code = codeElement ? codeElement.innerText : block.innerText;
                
                const div = document.createElement('div');
                div.className = 'mermaid';
                div.textContent = code;
                
                container.parentNode.replaceChild(div, container);
            });
            
            // Initialize mermaid
            mermaid.initialize({
                startOnLoad: true,
                theme: 'default',
                flowchart: { useMaxWidth: true, htmlLabels: true },
                sequence: { useMaxWidth: true }
            });
        });
    </script>
</head>
<body>
    {html_content}
</body>
</html>
"""

def locate_browser():
    """Find installed browser path."""
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def main():
    print("[INFO] [1/4] Using pandoc to compile Markdown to HTML Body...")
    try:
        # Run pandoc
        subprocess.run(["pandoc", MD_FILE, "-o", TEMP_HTML_BODY], check=True)
    except Exception as e:
        print(f"[ERROR] Failed to run pandoc. Please check if pandoc is installed: {e}")
        sys.exit(1)
        
    print("[INFO] [2/4] Assembling HTML, injecting CSS styling and Mermaid.js script...")
    if not os.path.exists(TEMP_HTML_BODY):
        print("[ERROR] Temp HTML body file not found!")
        sys.exit(1)
        
    with open(TEMP_HTML_BODY, "r", encoding="utf-8") as f:
        html_content = f.read()
        
    # Build complete HTML using simple string replace (safe against single braces in JS/CSS)
    final_html_content = HTML_TEMPLATE.replace("{css_styling}", CSS_STYLING).replace("{html_content}", html_content)
    
    with open(FINAL_HTML, "w", encoding="utf-8") as f:
        f.write(final_html_content)
        
    # Clean up raw body
    if os.path.exists(TEMP_HTML_BODY):
        os.remove(TEMP_HTML_BODY)
        
    print("[INFO] [3/4] Locating system browser (Chrome/Edge)...")
    browser_path = locate_browser()
    if not browser_path:
        print("[ERROR] Neither Google Chrome nor Microsoft Edge was found. Cannot generate PDF.")
        sys.exit(1)
    print(f"[INFO] Found browser at: {browser_path}")
    
    print("[INFO] [4/4] Launching headless browser to render JavaScript (Mermaid) and printing to PDF...")
    
    # Chrome/Edge flags to render JS (Mermaid) and print to PDF
    cmd = [
        browser_path,
        "--headless",
        "--disable-gpu",
        f"--print-to-pdf={PDF_FILE}",
        "--timeout=5000",
        "--virtual-time-budget=5000",
        "--run-all-compositor-stages-before-draw",
        FINAL_HTML
    ]
    
    try:
        subprocess.run(cmd, check=True)
        print("[SUCCESS] PDF successfully generated and saved to:")
        print(f"[SUCCESS] Path: {PDF_FILE}")
    except Exception as e:
        print(f"[ERROR] Failed to render PDF via headless browser: {e}")
        sys.exit(1)
    finally:
        # Clean up temporary full HTML file
        if os.path.exists(FINAL_HTML):
            os.remove(FINAL_HTML)

if __name__ == "__main__":
    main()
