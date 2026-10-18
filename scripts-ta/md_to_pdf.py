#!/usr/bin/env python3
"""
Simple markdown to PDF converter using fpdf2
Handles unicode by normalizing to ASCII
"""

from pathlib import Path
from fpdf import FPDF
import re

def normalize(text):
    """Replace unicode chars with ASCII - comprehensive"""
    # First do specific replacements
    text = text.replace('\u2013', '-').replace('\u2014', '--')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2022', '*').replace('\u2026', '...')
    text = text.replace('\u00a0', ' ').replace('\u00b7', '*')
    text = text.replace('\u2265', '>=').replace('\u2264', '<=')
    text = text.replace('\u2192', '->').replace('\u2190', '<-')
    text = text.replace('\u25b6', '>').replace('\u25c0', '<')
    text = text.replace('\u2713', 'OK').replace('\u2714', 'OK').replace('\u2611', '[x]')
    text = text.replace('\u26a0', '!').replace('\u26a1', '!').replace('\u2757', '!')
    text = text.replace('\u2705', 'OK').replace('\u274c', 'X').replace('\u274e', 'X')
    text = text.replace('\u2b50', '*').replace('\u2605', '*').replace('\u2606', '*')
    text = text.replace('\u23f3', '[time]').replace('\u231b', '[time]')
    text = text.replace('\u2728', '*').replace('\u2b50', '*')
    text = text.replace('\u2191', '^').replace('\u2193', 'v').replace('\u2194', '<->')
    text = text.replace('\u2195', '^v').replace('\u21d2', '=>').replace('\u21d4', '<=>')
    
    # Box drawing chars and all other unicode blocks
    result = []
    for char in text:
        code = ord(char)
        if code <= 127:
            result.append(char)
        elif code in (0x2500, 0x2502, 0x251c, 0x2524, 0x253c, 0x2550, 0x2551,
                      0x2560, 0x2563, 0x2566, 0x2569, 0x256c, 0x250c, 0x2510,
                      0x2514, 0x2518, 0x252c, 0x2534, 0x2554, 0x2557, 0x255a,
                      0x255d, 0x2555, 0x2556, 0x255b, 0x255c, 0x255e, 0x255f,
                      0x2561, 0x2562, 0x2564, 0x2565, 0x2567, 0x2568, 0x256a, 0x256b):
            result.append(' ')
        elif code <= 0x00ff:  # Latin-1 supplement
            result.append(char)
        else:
            # Skip or replace other unicode
            result.append('?')
    
    return ''.join(result)

def md_to_pdf(input_path, output_path):
    text = Path(input_path).read_text()
    
    # Remove YAML frontmatter
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    text = normalize(text)
    
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # Title page
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 15, 'ASEAN-Balkans Economic Cooperation', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 12, 'Halal Trade, Youth Entrepreneurship', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.cell(0, 12, 'and Knowledge Transfer Corridor', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.ln(10)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, 'Starting with Kosovo Through Diplomatic Engagement', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.cell(0, 8, 'Version 2.1 - Generic / External-Facing Draft', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.cell(0, 8, '19 July 2026', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.cell(0, 8, 'Prepared by: ASEAN-Based Sustainable Development Organisation', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.cell(0, 8, 'Classification: Working Draft - For Diplomatic Review', new_x="LMARGIN", new_y="NEXT", align='C')
    
    # Content
    lines = text.split('\n')
    in_table = False
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            if not in_table:
                pdf.ln(2)
            continue
        
        # Skip tables
        if stripped.startswith('|') or stripped.startswith('---') or stripped.startswith('!!!'):
            in_table = True
            continue
        else:
            in_table = False
        
        # Skip ascii art blocks
        if any(stripped.startswith(c) for c in ['+--', '+-', '| ', '    │', '    ├', '    └', '    ┌']):
            continue
        
        # Clean markdown
        clean = stripped.replace('**', '').replace('*', '').replace('`', '')
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
        clean = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', clean)
        
        if not clean:
            continue
        
        # Headers
        if clean.startswith('# '):
            pdf.add_page()
            pdf.set_font('Helvetica', 'B', 16)
            pdf.set_text_color(0, 51, 102)
            pdf.cell(0, 12, clean[2:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        elif clean.startswith('## '):
            pdf.set_font('Helvetica', 'B', 13)
            pdf.set_text_color(0, 51, 102)
            pdf.ln(3)
            pdf.cell(0, 10, clean[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        elif clean.startswith('### '):
            pdf.set_font('Helvetica', 'B', 11)
            pdf.set_text_color(0, 51, 102)
            pdf.ln(2)
            pdf.cell(0, 8, clean[4:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        elif clean.startswith('#### '):
            pdf.set_font('Helvetica', 'B', 9)
            pdf.set_text_color(0, 51, 102)
            pdf.cell(0, 7, clean[5:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        elif clean.startswith('##### '):
            pdf.set_font('Helvetica', 'B', 8)
            pdf.set_text_color(0, 51, 102)
            pdf.cell(0, 6, clean[6:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        
        # Blockquotes
        elif clean.startswith('> '):
            pdf.set_font('Helvetica', 'I', 9)
            pdf.set_text_color(80, 80, 80)
            pdf.set_x(20)
            pdf.multi_cell(pdf.w - 30, 5, clean[2:])
            pdf.set_text_color(0, 0, 0)
            pdf.set_x(10)
        
        # Bullet points
        elif clean.startswith('- ') or clean.startswith('* '):
            pdf.set_font('Helvetica', '', 9)
            pdf.set_x(15)
            pdf.cell(5, 5, '*')
            pdf.set_x(20)
            pdf.multi_cell(pdf.w - 30, 5, clean[2:])
        
        # Numbered lists
        elif re.match(r'^\d+\.\s', clean):
            pdf.set_font('Helvetica', '', 9)
            pdf.set_x(15)
            match = re.match(r'^(\d+)\.\s(.+)', clean)
            if match:
                pdf.cell(10, 5, match.group(1) + '.')
                pdf.set_x(25)
                pdf.multi_cell(pdf.w - 35, 5, match.group(2))
        
        # Regular text
        else:
            pdf.set_font('Helvetica', '', 9)
            pdf.set_x(10)
            pdf.multi_cell(pdf.w - 20, 5, clean)
    
    # Footer on content pages
    # (handled by page breaks)
    
    pdf.output(str(output_path))
    print(f"✅ PDF saved: {output_path}")
    print(f"   Pages: {pdf.page_no()}")
    print(f"   Size: {output_path.stat().st_size / 1024:.0f} KB")

if __name__ == "__main__":
    input_path = Path.home() / "Documents/Obsidian Vault/Inbox/Notion/ASEAN-Balkans-Corridor-Kosovo-Generic-v2.md"
    output_path = Path.home() / "Documents/Obsidian Vault/Inbox/Notion/ASEAN-Balkans-Corridor-Kosovo-Generic-v2.pdf"
    md_to_pdf(input_path, output_path)
