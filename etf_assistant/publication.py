"""Strict parsing of published JSON and the corresponding static HTML block."""
from html.parser import HTMLParser
import json


class SnapshotBlockError(ValueError):
    """The HTML does not contain exactly one complete plain-text snapshot block."""


class JSONIntegrityError(ValueError):
    """A JSON document uses ambiguous or non-standard values."""


def strict_json_loads(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise JSONIntegrityError("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(value):
        raise JSONIntegrityError("nonstandard_json_number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)


def extract_snapshot(text):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.active = False
            self.blocks = 0
            self.parts = []
            self.invalid = False

        def handle_starttag(self, tag, attrs):
            ids = [value for key, value in attrs if key == "id"]
            if self.active:
                self.invalid = True
            if tag == "pre" and "snapshot-json" in ids:
                self.blocks += 1
                if len(ids) != 1:
                    self.invalid = True
                self.active = True

        def handle_endtag(self, tag):
            if self.active:
                if tag == "pre":
                    self.active = False
                else:
                    self.invalid = True

        def handle_startendtag(self, tag, attrs):
            if self.active or (tag == "pre" and ("id", "snapshot-json") in attrs):
                self.invalid = True

        def handle_comment(self, data):
            if self.active:
                self.invalid = True

        def handle_data(self, data):
            if self.active:
                self.parts.append(data)

    parser = Parser()
    parser.feed(text)
    parser.close()
    if parser.blocks != 1 or parser.active or parser.invalid:
        raise SnapshotBlockError("snapshot_json_block_missing_or_ambiguous")
    return strict_json_loads("".join(parser.parts))
