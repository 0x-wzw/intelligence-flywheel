#!/usr/bin/env python3
"""
Professional PDF generator for diplomatic concept papers.
Anti-slope design: serif body, sans-serif headings, generous whitespace,
proper cursor management to prevent text overlaps.
"""

from pathlib import Path
from fpdf import FPDF
import re

def normalize(text):
    result = []
    for char in text:
        code = ord(char)
        if code <= 127:
            result.append(char)
        elif code in (0x2013, 0x2014):
            result.append('-')
        elif code in (0x2018, 0x2019):
            result.append("'")
        elif code in (0x201c, 0x201d):
            result.append('"')
        elif code == 0x2022:
            result.append('*')
        elif code == 0x2026:
            result.append('...')
        elif code == 0x00a0:
            result.append(' ')
        elif code == 0x2192:
            result.append('-')
        elif code == 0x25b6:
            result.append('>')
        elif code in (0x2713, 0x2705):
            result.append('[OK]')
        elif code in (0x25aa, 0x25a0):
            result.append('#')
        elif 0x2500 <= code <= 0x257f:
            result.append(' ')
        elif code in (0x2588, 0x2593):
            result.append('#')
        elif 0x00c0 <= code <= 0x00ff:
            result.append(char)
        else:
            result.append('')
    return ''.join(result)

def clean_md(text):
    text = text.replace('**', '').replace('*', '').replace('`', '')
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    return text

class DiplomaticPDF(FPDF):
    def __init__(self):
        super().__init__(unit='mm', format='A4')
        self.set_auto_page_break(auto=True, margin=22)
        self.left_margin = 20
        self.right_margin = 20
        self.content_width = 170
        self.set_margins(self.left_margin, 25, self.right_margin)
        
    def header(self):
        if self.page_no() == 1:
            return
        self.set_y(10)
        self.set_font('Helvetica', '', 7)
        self.set_text_color(120, 120, 120)
        self.cell(self.content_width / 2, 5, 'ASEAN-Balkans Economic Cooperation Concept Paper', align='L')
        self.cell(self.content_width / 2, 5, f'Page {self.page_no()}', align='R')
        self.set_draw_color(180, 180, 180)
        self.set_line_width(0.2)
        self.line(self.left_margin, 18, 190, 18)
        self.ln(14)
        
    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font('Helvetica', '', 7)
        self.set_text_color(130, 130, 130)
        self.cell(self.content_width, 5, 'Draft - 19 July 2026 - For Diplomatic and Stakeholder Review', align='C')

    def _reset_x(self):
        self.set_x(self.left_margin)

    def title_page(self):
        self.add_page()
        
        # Classification banner
        self.set_y(30)
        self.set_font('Helvetica', 'B', 8)
        self.set_text_color(140, 0, 0)
        self.cell(self.content_width, 6, 'WORKING DRAFT - FOR DIPLOMATIC AND STAKEHOLDER REVIEW', align='C')
        self.ln(4)
        self.set_draw_color(140, 0, 0)
        self.set_line_width(0.3)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(22)
        
        # Title - serif
        self.set_font('Times', 'B', 24)
        self.set_text_color(15, 15, 15)
        self.cell(self.content_width, 15, 'ASEAN-Balkans Economic Cooperation', align='C')
        self.ln(12)
        
        self.set_font('Times', 'B', 17)
        self.set_text_color(35, 35, 35)
        self.cell(self.content_width, 12, 'Halal Trade, Youth Entrepreneurship', align='C')
        self.ln(10)
        self.cell(self.content_width, 12, 'and Knowledge Transfer Corridor', align='C')
        self.ln(18)
        
        # Separator
        self.set_draw_color(160, 160, 160)
        self.set_line_width(0.2)
        self.line(75, self.get_y(), 135, self.get_y())
        self.ln(14)
        
        # Subtitle
        self.set_font('Helvetica', '', 10)
        self.set_text_color(55, 55, 55)
        self.cell(self.content_width, 7, 'Starting with Kosovo Through Diplomatic Engagement', align='C')
        self.ln(10)
        
        # Meta
        self.set_font('Helvetica', '', 9)
        self.set_text_color(100, 100, 100)
        self.cell(self.content_width, 6, 'Version 2.1  |  Generic / External-Facing Draft', align='C')
        self.ln(6)
        self.cell(self.content_width, 6, '19 July 2026', align='C')
        self.ln(22)
        
        # Organization
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(35, 35, 35)
        self.cell(self.content_width, 7, 'Prepared by:', align='C')
        self.ln(8)
        self.set_font('Helvetica', '', 10)
        self.cell(self.content_width, 7, 'ASEAN-Based Sustainable Development Organisation', align='C')
        self.ln(35)
        
        # Contact
        self.set_font('Helvetica', '', 8)
        self.set_text_color(120, 120, 120)
        self.cell(self.content_width, 5, 'For diplomatic coordination: director@organisation.net', align='C')
        self.ln(5)
        self.cell(self.content_width, 5, 'For media inquiries: communications@organisation.net', align='C')
        
    def h1(self, text):
        self.add_page()
        self.set_font('Helvetica', 'B', 17)
        self.set_text_color(0, 35, 75)
        self.cell(self.content_width, 14, text, border='B')
        self.ln(10)
        self.set_text_color(0, 0, 0)
        
    def h2(self, text):
        self.ln(8)
        self.set_font('Helvetica', 'B', 13)
        self.set_text_color(0, 45, 90)
        self.cell(self.content_width, 10, text)
        self.ln(7)
        self.set_text_color(0, 0, 0)
        
    def h3(self, text):
        self.ln(6)
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(0, 55, 110)
        self.cell(self.content_width, 8, text)
        self.ln(6)
        self.set_text_color(0, 0, 0)
        
    def h4(self, text):
        self.ln(5)
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(45, 45, 45)
        self.cell(self.content_width, 7, text)
        self.ln(5)
        self.set_text_color(0, 0, 0)
        
    def paragraph(self, text):
        self._reset_x()
        self.set_font('Times', '', 11)
        self.set_text_color(28, 28, 28)
        self.multi_cell(self.content_width, 5.5, text)
        self.ln(4)
        
    def bullet(self, text, level=0):
        indent = self.left_margin + 5 + (level * 6)
        marker_x = indent
        text_x = indent + 5
        text_width = 190 - text_x - self.right_margin
        
        self.set_xy(marker_x, self.get_y())
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(0, 55, 110)
        self.cell(4, 5.5, '-', align='L')
        
        self.set_xy(text_x, self.get_y())
        self.set_font('Times', '', 11)
        self.set_text_color(28, 28, 28)
        self.multi_cell(text_width, 5.5, text)
        self.ln(2)
        
    def blockquote(self, text):
        self._reset_x()
        self.set_font('Times', 'I', 10)
        self.set_text_color(75, 75, 75)
        start_y = self.get_y()
        self.set_x(self.left_margin + 4)
        self.multi_cell(self.content_width - 8, 5.2, text)
        end_y = self.get_y()
        self.set_draw_color(170, 170, 170)
        self.set_line_width(0.3)
        self.line(self.left_margin + 1, start_y, self.left_margin + 1, end_y)
        self.ln(5)
        self.set_text_color(0, 0, 0)
        
    def numbered(self, num, text):
        num_width = 12
        text_x = self.left_margin + num_width
        text_width = 190 - text_x - self.right_margin
        
        self.set_xy(self.left_margin, self.get_y())
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(0, 55, 110)
        self.cell(num_width, 5.5, f'{num}.', align='L')
        
        self.set_xy(text_x, self.get_y())
        self.set_font('Times', '', 11)
        self.set_text_color(28, 28, 28)
        self.multi_cell(text_width, 5.5, text)
        self.ln(2)
    def render_table(self, rows):
        """Render a markdown table with text wrapping — no overflow"""
        if not rows or len(rows) < 2:
            return
        
        num_cols = max(len(r) for r in rows)
        if num_cols == 0:
            return
        
        # Check page break before table
        estimated_height = len(rows) * 8
        if self.get_y() + estimated_height > 270:
            self.add_page()
        
        col_w = self.content_width / num_cols
        padding = 2  # mm padding inside cells
        effective_w = col_w - (padding * 2)
        
        # Calculate row heights based on content
        row_heights = []
        for row in rows:
            max_h = 6  # minimum row height
            for cell in row:
                # Estimate lines needed
                chars_per_line = max(1, int(effective_w / 1.8))  # approx chars at font size 8
                lines_needed = max(1, (len(cell) + chars_per_line - 1) // chars_per_line)
                cell_h = lines_needed * 4 + 2  # 4mm per line + padding
                max_h = max(max_h, cell_h)
            row_heights.append(max_h)
        
        # Header row
        header_h = row_heights[0]
        self.set_font('Helvetica', 'B', 8)
        self.set_fill_color(235, 243, 250)
        self.set_text_color(0, 35, 75)
        self.set_draw_color(0, 50, 100)
        self.set_line_width(0.3)
        
        start_y = self.get_y()
        for i, cell in enumerate(rows[0]):
            x = self.left_margin + (i * col_w)
            self.set_xy(x, start_y)
            # Draw cell background and border
            self.cell(col_w, header_h, '', 1, 0, 'L', True)
            # Draw text with padding
            self.set_xy(x + padding, start_y + padding)
            self.multi_cell(effective_w, 4, cell[:80], 0, 'L')
        
        # Move to after header
        self.set_y(start_y + header_h)
        
        # Data rows
        self.set_font('Helvetica', '', 8)
        self.set_text_color(28, 28, 28)
        self.set_fill_color(255, 255, 255)
        
        for row_idx, row in enumerate(rows[1:], 1):
            row_h = row_heights[row_idx]
            start_y = self.get_y()
            
            # Check page break mid-table
            if start_y + row_h > 270:
                self.add_page()
                start_y = self.get_y()
            
            # Draw borders first (full row)
            for i in range(num_cols):
                x = self.left_margin + (i * col_w)
                self.set_xy(x, start_y)
                self.cell(col_w, row_h, '', 'LR', 0, 'L')
            
            # Draw text
            for i, cell in enumerate(row):
                x = self.left_margin + (i * col_w)
                self.set_xy(x + padding, start_y + padding)
                self.multi_cell(effective_w, 4, cell[:100], 0, 'L')
            
            self.set_y(start_y + row_h)
        
        # Bottom border
        self.set_draw_color(0, 50, 100)
        self.set_line_width(0.3)
        y = self.get_y()
        self.line(self.left_margin, y, self.left_margin + self.content_width, y)
        self.ln(6)


def md_to_pdf(input_path, output_path):
    text = Path(input_path).read_text()
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    text = normalize(text)
    
    pdf = DiplomaticPDF()
    pdf.title_page()
    
    lines = text.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        if not stripped:
            pdf.ln(3)
            i += 1
            continue
        
        # Markdown tables
        if stripped.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            
            rows = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.split('|')]
                cells = [c for c in cells if c]
                if cells and not all(set(c) <= set('-: ') for c in cells):
                    rows.append(cells)
            
            if rows:
                pdf.render_table(rows)
            continue
        
        # Skip code blocks and ASCII art
        if stripped.startswith('```') or stripped.startswith('!!!'):
            i += 1
            continue
        if any(stripped.startswith(c) for c in ['+--', '+-', '| ', '    │', '    ├', '    └', '    ┌', '    ▼']):
            i += 1
            continue
        if '│' in stripped and ('├' in stripped or '└' in stripped or '┌' in stripped):
            i += 1
            continue
        if '▶' in stripped or '▼' in stripped:
            i += 1
            continue
        
        clean = clean_md(stripped)
        if not clean:
            i += 1
            continue
        
        # Render by type
        if clean.startswith('# '):
            pdf.h1(clean[2:])
        elif clean.startswith('## '):
            pdf.h2(clean[3:])
        elif clean.startswith('### '):
            pdf.h3(clean[4:])
        elif clean.startswith('#### '):
            pdf.h4(clean[5:])
        elif clean.startswith('##### '):
            pdf.h4(clean[6:])
        elif clean.startswith('> '):
            pdf.blockquote(clean[2:])
        elif clean.startswith('- ') or clean.startswith('* '):
            pdf.bullet(clean[2:])
        elif re.match(r'^\d+\.\s', clean):
            m = re.match(r'^(\d+)\.\s(.+)', clean)
            if m:
                pdf.numbered(m.group(1), m.group(2))
        else:
            pdf.paragraph(clean)
        
        i += 1
    
    pdf.output(str(output_path))
    size_kb = output_path.stat().st_size / 1024
    print(f"✅ PDF saved: {output_path}")
    print(f"   Pages: {pdf.page_no()}")
    print(f"   Size: {size_kb:.0f} KB")


if __name__ == "__main__":
    input_path = Path.home() / "Documents/Obsidian Vault/Inbox/Notion/ASEAN-Balkans-Corridor-Kosovo-Generic-v2.md"
    output_path = Path.home() / "Documents/Obsidian Vault/Inbox/Notion/ASEAN-Balkans-Corridor-Kosovo-Generic-v2.pdf"
    md_to_pdf(input_path, output_path)
