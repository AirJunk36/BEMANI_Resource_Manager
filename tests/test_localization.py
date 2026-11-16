"""Keep parser diagnostics in the shared native UI translation catalog."""
import ast
import json
from pathlib import Path
import re
import unittest

HOME=Path(__file__).resolve().parents[1]


def message(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
    if isinstance(node,ast.JoinedStr):
        parts=[];slot=0
        for part in node.values:
            if isinstance(part,ast.Constant):parts.append(part.value)
            else:parts.append('{'+str(slot)+'}');slot+=1
        return ''.join(parts)
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
        left,right=message(node.left),message(node.right)
        if left is not None:return left+(right if right is not None else '{0}')


class LocalizationTests(unittest.TestCase):
    def test_generated_export_names_are_english_and_replacement_suffix_is_stable(self):
        from iidxviewer.exporting import safe_name
        self.assertEqual(safe_name('画像.png'),'asset.png')
        self.assertEqual(safe_name('café.wav',replaced=True),'cafe_replaced.wav')
        self.assertEqual(safe_name('title_replaced.wav',replaced=True),'title_replaced.wav')
    def test_all_parser_exception_templates_have_english_translations(self):
        catalog=json.loads((HOME/'iidxviewer/locales/en.json').read_text(encoding='utf8'))
        missing=[]
        for path in [HOME/'backend.py',*(HOME/'iidxviewer').glob('*.py')]:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf8'))):
                args=()
                if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call):args=node.exc.args
                elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'append','add'}:
                    if any(word in ast.unparse(node.func.value) for word in ('warning','issue','message')):args=node.args
                if not args:continue
                text=message(args[0])
                if text and re.search('[\u4e00-\u9fff]',text):
                    if text not in catalog:missing.append(f'{path.name}:{node.lineno}: {text}')
                    else:
                        self.assertFalse(re.search('[\u4e00-\u9fff]',catalog[text]),text)
                        self.assertEqual(set(re.findall(r'\{\d+\}',text)),set(re.findall(r'\{\d+\}',catalog[text])),text)
        self.assertEqual(missing,[])
