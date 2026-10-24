"""Read the exact static JSON block; never use page summaries or scraped prices."""
from html.parser import HTMLParser
import json


def extract_snapshot(text):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.active = False
            self.blocks = 0
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag == "pre" and dict(attrs).get("id") == "snapshot-json":
                self.blocks += 1
                self.active = True

        def handle_endtag(self, tag):
            if tag == "pre":
                self.active = False

        def handle_data(self, data):
            if self.active:
                self.parts.append(data)

    parser = Parser()
    parser.feed(text)
    if parser.blocks != 1 or parser.active:
        raise ValueError("snapshot_json_block_missing_or_ambiguous")
    return json.loads("".join(parser.parts))
