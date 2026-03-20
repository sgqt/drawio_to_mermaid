#!/usr/bin/env python3
"""Test Chinese label handling."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from drawio_to_mermaid import DrawioToMermaid

# Create a simple Draw.io file with Chinese labels
drawio_content = '''<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net">
  <diagram name="Page 1">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="start" value="开始" style="ellipse" vertex="1" parent="1">
          <mxGeometry x="50" y="50" width="80" height="60" as="geometry" />
        </mxCell>
        <mxCell id="process" value="处理" style="rounded=0" vertex="1" parent="1">
          <mxGeometry x="200" y="50" width="80" height="60" as="geometry" />
        </mxCell>
        <mxCell id="end" value="结束" style="ellipse" vertex="1" parent="1">
          <mxGeometry x="350" y="50" width="80" height="60" as="geometry" />
        </mxCell>
        <mxCell id="e1" value="下一步" edge="1" source="start" target="process" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e2" value="完成" edge="1" source="process" target="end" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>'''

# Write to file
test_file = Path("chinese_test.drawio")
test_file.write_text(drawio_content, encoding='utf-8')

# Convert it
print("Testing Chinese labels...")
converter = DrawioToMermaid(test_file)
mermaid_code = converter.convert()
print("Result:")
print(mermaid_code)

# Clean up
test_file.unlink()
