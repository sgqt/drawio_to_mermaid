#!/usr/bin/env python3
"""
Draw.io to Mermaid Converter

Converts Draw.io diagrams to Mermaid format.
Supports flowcharts, decision diagrams, grouped elements, and more.

This script uses only Python standard library - no external dependencies required.
"""

import argparse
import base64
import binascii
import gzip
import logging
import re
import sys
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from urllib.parse import unquote


# Custom Exceptions
class DrawioDecompressionError(Exception):
    """Raised when diagram data cannot be decompressed."""
    pass


class DrawioParsingError(Exception):
    """Raised when XML parsing fails."""
    pass


# Shape mapping configuration
SHAPE_MAPPINGS = {
    # Draw.io shape -> Mermaid syntax template
    # Templates use {label} and {id} as placeholders
    "rhombus": '{id}{{"{label}"}}',
    "decision": '{id}{{"{label}"}}',
    "ellipse": '{id}(("{label}"))',
    "circle": '{id}(("{label}"))',
    "doubleEllipse": '{id}(("{label}"))',
    "stadium": '{id}("{label}")',
    "rounded": '{id}("{label}")',
    "rect": '{id}["{label}"]',
    "rectangle": '{id}["{label}"]',
    "process": '{id}["{label}"]',
    "parallelogram": '{id}[/"{label}"/]',
    "predefinedProcess": '{id}[/"{label}"/]',
    "document": '{id}[/"{label}"/]',
    "cylinder": '{id}[("{label}")]',
    "database": '{id}[("{label}")]',
}


class DrawioToMermaid:
    """
    Main converter class for Draw.io to Mermaid conversion.
    """

    # Default decompression wbits values to try
    DECOMPRESSION_ATTEMPTS = [
        (-15, "raw deflate"),
        (47, "deflate with zlib header & 32k window"),
        (31, "deflate with zlib header & 16k window"),
        (15, "deflate with zlib header & 8k window"),
        (0, "auto-detect zlib/gzip header"),
    ]

    def __init__(self, input_file: Path, strict: bool = False, log_level: int = logging.WARNING):
        """
        Initialize the converter.

        Args:
            input_file: Path to the Draw.io file
            strict: If True, errors will raise exceptions. Otherwise, errors are logged and skipped.
            log_level: Logging level (default: WARNING)
        """
        self.input_file = input_file
        self.strict = strict
        self.logger = self._setup_logger(log_level)
        self.diagram_pages: List[str] = []

    def _setup_logger(self, level: int) -> logging.Logger:
        """Setup logger with the specified level."""
        logger = logging.getLogger(self.__class__.__name__)
        logger.setLevel(level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def load_file(self) -> str:
        """
        Load the content of the Draw.io file.

        Returns:
            The file content as a string.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            IOError: If the file cannot be read.
        """
        try:
            with open(self.input_file, 'r', encoding='utf-8') as f:
                data = f.read()
            self.logger.info(f"Loaded file: {self.input_file}")
            return data
        except FileNotFoundError:
            self.logger.error(f"File not found: {self.input_file}")
            raise
        except Exception as e:
            self.logger.error(f"Error loading file: {e}")
            raise

    def _parse_style(self, style_str: str) -> Dict[str, str]:
        """
        Parse a Draw.io style string into a dictionary.

        Args:
            style_str: Semicolon-separated key=value pairs (e.g., "shape=ellipse;whiteSpace=wrap")

        Returns:
            Dictionary of style attributes
        """
        style_dict = {}
        if not style_str:
            return style_dict

        for token in style_str.split(';'):
            if '=' in token:
                key, value = token.split('=', 1)
                style_dict[key] = value
            elif token:
                style_dict[token] = "1"

        return style_dict

    def _decompress_data(self, xml_data: str) -> None:
        """
        Decompress Draw.io data if needed.

        Handles multiple compression formats:
        - Raw deflate
        - Zlib compressed
        - Gzip compressed
        - Base64 encoded
        - URL encoded
        - Uncompressed XML

        Populates self.diagram_pages with decompressed XML strings.
        """
        diagrams = []

        # First try to parse as mxfile to handle multi-page correctly
        try:
            root = ET.fromstring(xml_data)
            if root.tag == 'mxfile':
                self.logger.debug("Found mxfile format")
                diagrams_et = root.findall('diagram')
                for diagram in diagrams_et:
                    # First check for nested mxGraphModel (uncompressed)
                    model = diagram.find('mxGraphModel')
                    if model is not None:
                        diagrams.append(ET.tostring(model, encoding='unicode'))
                    else:
                        # Get text content (compressed/encoded)
                        content = diagram.text or ""
                        if content.strip():
                            diagrams.append(content)
                # If we found diagrams in mxfile, process them and return
                if diagrams:
                    self._process_diagrams(diagrams)
                    return
        except ET.ParseError:
            pass

        # Check if already uncompressed single page (no diagram tags)
        if "<mxGraphModel" in xml_data and "<diagram" not in xml_data:
            self.logger.debug("Found uncompressed <mxGraphModel>")
            self.diagram_pages.append(xml_data)
            return

        # Find diagram tags using regex
        diagrams = re.findall(r"<diagram[^>]*>(.*?)</diagram>", xml_data, re.DOTALL)

        if not diagrams:
            # Last resort - check if it's plain XML
            if "<mxGraphModel" in xml_data:
                self.diagram_pages.append(xml_data)
                return
            else:
                msg = "No valid Draw.io content found"
                self.logger.error(msg)
                if self.strict:
                    raise DrawioDecompressionError(msg)
                return

        self._process_diagrams(diagrams)

    def _process_diagrams(self, diagrams: List[str]) -> None:
        """
        Process a list of diagram data strings.

        Args:
            diagrams: List of diagram data strings (possibly compressed/encoded)
        """
        self.logger.debug(f"Found {len(diagrams)} diagram(s)")

        for idx, diagram in enumerate(diagrams):
            diagram = diagram.strip()
            if not diagram:
                self.logger.warning(f"Diagram {idx} is empty, skipping")
                continue

            decompressed = self._try_decompress(diagram, idx)
            if decompressed:
                self.diagram_pages.append(decompressed)

    def _try_decompress(self, data: str, index: int) -> Optional[str]:
        """
        Try to decompress a single diagram data.

        Args:
            data: The diagram data (possibly compressed/encoded)
            index: Diagram index for logging

        Returns:
            Decompressed XML string or None if failed
        """
        # Check if already XML
        if data.startswith('<') and "<mxGraphModel" in data:
            self.logger.debug(f"Diagram {index} is already XML")
            return data

        # Try URL decoding
        try:
            decoded = unquote(data)
            if "<mxGraphModel" in decoded:
                self.logger.debug(f"Diagram {index} was URL encoded")
                return decoded
        except Exception:
            pass

        # Try base64 decoding
        decoded_bytes = self._try_base64_decode(data, index)
        if decoded_bytes is None:
            if self.strict:
                raise DrawioDecompressionError(f"Base64 decoding failed for diagram {index}")
            return None

        # Check if decoded is XML
        try:
            xml_check = decoded_bytes.decode('utf-8')
            if xml_check.startswith('<') and "<mxGraphModel" in xml_check:
                self.logger.debug(f"Diagram {index} was base64 encoded XML")
                return xml_check
        except UnicodeDecodeError:
            pass

        # Try various decompression methods
        for wbits, desc in self.DECOMPRESSION_ATTEMPTS:
            result = self._try_decompress_with_wbits(decoded_bytes, wbits, desc, index)
            if result:
                return result

        # Try gzip
        result = self._try_gzip_decompress(decoded_bytes, index)
        if result:
            return result

        # Try PAKO variant
        result = self._try_pako_decompress(decoded_bytes, index)
        if result:
            return result

        msg = f"Failed to decompress diagram {index}"
        self.logger.error(msg)
        if self.strict:
            raise DrawioDecompressionError(msg)
        return None

    def _try_base64_decode(self, data: str, index: int) -> Optional[bytes]:
        """Try to base64 decode data, handling padding issues."""
        try:
            # Validate input contains at least some valid base64 characters
            # and not too many invalid characters
            valid_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
            input_chars = set(c for c in data if c not in ' \t\n\r')
            if not input_chars or input_chars - valid_chars:
                # Too many invalid characters
                return None

            # Fix padding
            padding_needed = len(data) % 4
            if padding_needed:
                data += '=' * (4 - padding_needed)

            try:
                decoded = base64.b64decode(data)
                # Validate result - check if it looks like compressed data or XML
                if not decoded or len(decoded) < 4:
                    return None
                return decoded
            except binascii.Error:
                try:
                    decoded = base64.urlsafe_b64decode(data)
                    if not decoded or len(decoded) < 4:
                        return None
                    return decoded
                except binascii.Error:
                    return None
        except Exception:
            return None

    def _try_decompress_with_wbits(self, data: bytes, wbits: int, desc: str, index: int) -> Optional[str]:
        """Try decompression with specific wbits parameter."""
        try:
            if wbits == 0:
                decompressed = zlib.decompress(data, zlib.MAX_WBITS | 32)
            else:
                decompressed = zlib.decompress(data, wbits)

            xml_text = decompressed.decode('utf-8')
            if "<mxGraphModel" in xml_text:
                self.logger.info(f"Diagram {index} decompressed using {desc}")
                return xml_text
        except Exception:
            pass
        return None

    def _try_gzip_decompress(self, data: bytes, index: int) -> Optional[str]:
        """Try gzip decompression."""
        if len(data) >= 2 and data[:2] == b'\x1f\x8b':
            try:
                decompressed = gzip.decompress(data)
                xml_text = decompressed.decode('utf-8')
                if "<mxGraphModel" in xml_text:
                    self.logger.info(f"Diagram {index} decompressed using gzip")
                    return xml_text
            except Exception:
                pass
        return None

    def _try_pako_decompress(self, data: bytes, index: int) -> Optional[str]:
        """Try PAKO variant decompression."""
        try:
            inflator = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decompressed = inflator.decompress(data)
            xml_text = decompressed.decode('utf-8')
            if "<mxGraphModel" in xml_text:
                self.logger.info(f"Diagram {index} decompressed using PAKO variant")
                return xml_text
        except Exception:
            pass
        return None

    def _parse_xml(self, xml_data: str) -> Optional[ET.Element]:
        """
        Parse XML data into ElementTree.

        Args:
            xml_data: The XML string to parse

        Returns:
            Root element or None if parsing fails
        """
        try:
            # Clean up common issues
            xml_data = xml_data.replace('&nbsp;', '&#160;')

            # Add XML declaration if needed
            if not xml_data.strip().startswith('<?xml') and '<mxGraphModel' in xml_data:
                xml_data = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_data

            root = ET.fromstring(xml_data)

            # Navigate to mxGraphModel
            if root.tag == 'diagram':
                model = root.find('mxGraphModel')
                if model is not None:
                    root = model
            elif root.tag == 'mxfile':
                diagram = root.find('diagram')
                if diagram is not None:
                    model = diagram.find('mxGraphModel')
                    if model is not None:
                        root = model

            self.logger.info("XML parsing successful")
            return root

        except ET.ParseError as e:
            self.logger.error(f"XML parsing failed: {e}")
            if self.strict:
                raise DrawioParsingError(str(e))
            return None

    def _build_diagram(self, root: ET.Element) -> Dict:
        """
        Build internal diagram representation from XML root.

        Args:
            root: The mxGraphModel root element

        Returns:
            Dictionary with nodes, edges, and groups
        """
        nodes = []
        edges = []
        node_map = {}
        groups = {}

        # Find the root element containing cells
        diagram_root = root.find("root")
        if diagram_root is None:
            diagram_root = root

        # Process UserObject elements that wrap mxCell
        # Build mapping from UserObject to its nested mxCell
        for user_obj in diagram_root.iter("UserObject"):
            user_id = user_obj.get("id")
            user_label = user_obj.get("label") or ""
            user_label = self._strip_html_labels(user_label)
            
            # Find nested mxCell within this UserObject
            for cell in user_obj.findall("mxCell"):
                cell_id = cell.get("id")
                
                if cell.get("vertex") == "1":
                    # If mxCell has no id, use UserObject's id
                    if not cell_id:
                        cell_id = user_id
                    
                    if cell_id in ("0", "1"):
                        continue
                    
                    # Get label from mxCell value, or from UserObject
                    label = cell.get("value") or ""
                    if not label:
                        label = user_label
                    label = self._strip_html_labels(label)
                    
                    style = cell.get("style") or ""
                    geometry = cell.find("mxGeometry")

                    node = {
                        "id": cell_id,
                        "label": label,
                        "style": style,
                        "style_dict": self._parse_style(style),
                        "geometry": geometry.attrib if geometry is not None else {},
                        "parent": cell.get("parent")
                    }
                    if cell_id not in node_map:  # Don't duplicate if already processed
                        nodes.append(node)
                        node_map[cell_id] = node

                elif cell.get("edge") == "1":
                    # If mxCell has no id, use UserObject's id
                    if not cell_id:
                        cell_id = user_id
                    
                    # Get label from mxCell value, or from UserObject
                    label = cell.get("value") or ""
                    if not label:
                        label = user_label
                    label = self._strip_html_labels(label)
                    
                    edge = {
                        "id": cell_id,
                        "source": cell.get("source"),
                        "target": cell.get("target"),
                        "label": label,
                        "style": cell.get("style") or "",
                        "style_dict": self._parse_style(cell.get("style") or "")
                    }
                    edges.append(edge)

        # Process standalone mxCell elements (not inside UserObject)
        for cell in diagram_root.iter("mxCell"):
            cell_id = cell.get("id")
            if cell_id in ("0", "1"):
                continue
            
            # Skip if already processed as part of UserObject
            if cell_id in node_map:
                continue

            if cell.get("vertex") == "1":
                label = cell.get("value") or ""
                label = self._strip_html_labels(label)
                
                style = cell.get("style") or ""
                geometry = cell.find("mxGeometry")

                node = {
                    "id": cell_id,
                    "label": label,
                    "style": style,
                    "style_dict": self._parse_style(style),
                    "geometry": geometry.attrib if geometry is not None else {},
                    "parent": cell.get("parent")
                }
                nodes.append(node)
                node_map[cell_id] = node

            elif cell.get("edge") == "1":
                label = cell.get("value") or ""
                label = self._strip_html_labels(label)
                
                edge = {
                    "id": cell_id,
                    "source": cell.get("source"),
                    "target": cell.get("target"),
                    "label": label,
                    "style": cell.get("style") or "",
                    "style_dict": self._parse_style(cell.get("style") or "")
                }
                edges.append(edge)

        # Build groups
        for node in nodes:
            parent = node.get("parent")
            if parent and parent in node_map:
                parent_style = node_map[parent].get("style", "")
                if "group" in parent_style or "swimlane" in parent_style:
                    if parent not in groups:
                        groups[parent] = {
                            "label": node_map[parent].get("label") or f"Group_{parent}",
                            "children": []
                        }
                    groups[parent]["children"].append(node)

        self.logger.info(f"Built diagram: {len(nodes)} nodes, {len(edges)} edges, {len(groups)} groups")
        return {"nodes": nodes, "edges": edges, "groups": groups, "node_map": node_map}

    def _strip_html_labels(self, text: str) -> str:
        """
        Strip HTML tags from Draw.io label text.
        
        Draw.io labels often contain HTML like:
        <div style="..."><font style="...">Text<br/></font></div>
        
        Args:
            text: The label text that may contain HTML
            
        Returns:
            Plain text with HTML tags removed
        """
        if not text:
            return ""
        
        import html
        # First unescape HTML entities
        text = html.unescape(text)
        
        # Remove HTML tags but preserve text content
        # Handle <br/> and <br> tags specially
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        
        # Remove all other HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Clean up extra whitespace and newlines
        text = re.sub(r'\n\s*\n', '\n', text)
        text = text.strip()
        
        return text

    def _get_shape_syntax(self, node: Dict) -> str:
        """
        Get Mermaid shape syntax for a node.

        Args:
            node: Node dictionary

        Returns:
            Mermaid node definition string
        """
        label = node["label"].strip() if node["label"] else f"Node_{node['id']}"
        style = node["style_dict"]
        node_id = f"N{node['id']}"

        # Check for explicit shape
        shape = style.get("shape", "").lower()

        # Check for rounded corners
        if style.get("rounded") == "1" and not shape:
            shape = "rounded"

        # Check style string for embedded shape information
        style_str = node.get("style", "").lower()
        if "rhombus" in style_str:
            shape = "rhombus"
        elif "ellipse" in style_str or "circle" in style_str:
            shape = "ellipse"
        elif "stadium" in style_str:
            shape = "stadium"
        elif "cylinder" in style_str or "database" in style_str:
            shape = "cylinder"
        elif "parallelogram" in style_str:
            shape = "parallelogram"
        elif "document" in style_str:
            shape = "document"
        elif "rounded" in style_str:
            shape = "rounded"

        # Get template or use default
        template = SHAPE_MAPPINGS.get(shape, '{id}["{label}"]')

        return template.format(id=node_id, label=label)

    def _get_edge_syntax(self, edge: Dict) -> str:
        """
        Get Mermaid edge syntax.

        Args:
            edge: Edge dictionary

        Returns:
            Mermaid edge definition string
        """
        src = f"N{edge['source']}"
        tgt = f"N{edge['target']}"
        label = edge["label"].strip()
        style = edge["style_dict"]

        # Determine arrow type
        arrow = "-->"
        if style.get("dashed") == "1":
            arrow = "-.->"
        elif style.get("dotted") == "1":
            arrow = "-.->"

        # Check if no arrow
        if style.get("endArrow") == "none":
            # Replace -> with --- to get three dashes
            if arrow == "-->":
                arrow = "---"
            elif arrow == "-.->":
                arrow = "-.-"

        # Build edge definition
        if label:
            return f'{src} -- "{label}" {arrow} {tgt}'
        else:
            return f'{src} {arrow} {tgt}'

    def _emit_subgraph(self, group_id: str, group: Dict, emitted: set, indent: str = "    ") -> List[str]:
        """
        Emit subgraph for a group.

        Args:
            group_id: Group identifier
            group: Group dictionary
            emitted: Set of already emitted node IDs
            indent: Indentation string

        Returns:
            List of Mermaid syntax lines
        """
        lines = []
        label = group["label"]
        lines.append(f'{indent}subgraph {group_id}["{label}"]')

        for child in group.get("children", []):
            child_id = child["id"]
            if child_id not in emitted:
                lines.append(f'{indent}    {self._get_shape_syntax(child)}')
                emitted.add(child_id)

        lines.append(f'{indent}end')
        return lines

    def _detect_direction(self, diagram: Dict) -> str:
        """
        Detect flow direction from diagram layout.

        Args:
            diagram: Diagram dictionary

        Returns:
            Direction string (TD, LR, RL, BT)
        """
        # Check for directional hints in styles
        for node in diagram.get("nodes", []):
            style = node.get("style", "").lower()
            if "rhombus" in style or "decision" in style:
                # Decision diagrams often flow top-down
                return "TD"

        # Default to TD for most diagrams
        return "TD"

    def convert(self, diagram_index: int = 0, direction: Optional[str] = None) -> str:
        """
        Convert Draw.io to Mermaid format.

        Args:
            diagram_index: Which diagram page to convert (for multi-page files)
            direction: Flow direction (TD, LR, RL, BT). Auto-detected if None.

        Returns:
            Mermaid diagram code
        """
        # Load and decompress
        data = self.load_file()
        self.diagram_pages = []
        self._decompress_data(data)

        if not self.diagram_pages:
            msg = "No valid diagram pages found"
            self.logger.error(msg)
            if self.strict:
                raise DrawioDecompressionError(msg)
            return ""

        # Validate index
        if diagram_index < 0 or diagram_index >= len(self.diagram_pages):
            self.logger.warning(f"Diagram index {diagram_index} out of range, using 0")
            diagram_index = 0

        # Parse XML
        xml_data = self.diagram_pages[diagram_index]
        root = self._parse_xml(xml_data)
        if root is None:
            return ""

        # Build diagram
        diagram = self._build_diagram(root)

        # Detect direction if not specified
        if direction is None:
            direction = self._detect_direction(diagram)

        # Generate Mermaid code
        lines = [f"flowchart {direction}"]

        # Emit subgraphs first
        emitted = set()
        for group_id, group in diagram.get("groups", {}).items():
            lines.extend(self._emit_subgraph(group_id, group, emitted))

        # Emit remaining nodes
        for node in diagram.get("nodes", []):
            if node["id"] not in emitted:
                lines.append(self._get_shape_syntax(node))
                emitted.add(node["id"])

        # Emit edges
        node_map = diagram.get("node_map", {})
        for edge in diagram.get("edges", []):
            if edge["source"] in node_map and edge["target"] in node_map:
                lines.append(self._get_edge_syntax(edge))
            else:
                self.logger.warning(f"Skipping edge {edge['id']}: missing endpoint")

        return "\n".join(lines)

    def list_pages(self) -> List[int]:
        """
        List available diagram pages.

        Returns:
            List of available page indices
        """
        data = self.load_file()
        self.diagram_pages = []
        self._decompress_data(data)
        return list(range(len(self.diagram_pages)))


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Convert Draw.io diagrams to Mermaid format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s diagram.drawio -o output.mmd
  %(prog)s diagram.drawio -o output.mmd --direction LR
  %(prog)s diagram.drawio -o output.mmd --page 1
  %(prog)s diagram.drawio --list-pages
  %(prog)s diagram.drawio -o output.mmd --verbose
        """
    )

    parser.add_argument("input", type=Path, help="Input Draw.io file")
    parser.add_argument("-o", "--output", type=Path, help="Output Mermaid file")
    parser.add_argument("-d", "--direction", choices=["TD", "LR", "RL", "BT"],
                        help="Flow direction (default: auto-detect)")
    parser.add_argument("-p", "--page", type=int, default=0,
                        help="Diagram page to convert (for multi-page files, default: 0)")
    parser.add_argument("--list-pages", action="store_true",
                        help="List available diagram pages and exit")
    parser.add_argument("-s", "--strict", action="store_true",
                        help="Strict mode: errors raise exceptions instead of being skipped")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose output")
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING

    # Create converter
    converter = DrawioToMermaid(args.input, strict=args.strict, log_level=log_level)

    try:
        # List pages if requested
        if args.list_pages:
            pages = converter.list_pages()
            print(f"Available diagram pages: {pages}")
            print(f"Total: {len(pages)} page(s)")
            return 0

        # Convert
        mermaid_code = converter.convert(
            diagram_index=args.page,
            direction=args.direction
        )

        if not mermaid_code:
            print("Error: No output generated", file=sys.stderr)
            return 1

        # Output
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(mermaid_code)
            print(f"Converted to: {args.output}")
        else:
            print(mermaid_code)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
