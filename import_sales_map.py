"""Import a verified sales-map JSON export into the app's local spatial DB."""

import argparse
from imported_markers import DEFAULT_DB, import_json

parser = argparse.ArgumentParser()
parser.add_argument("source", help="sales-map-all-YYYY-MM-DD.json")
parser.add_argument("--output", default=str(DEFAULT_DB))
args = parser.parse_args()
count = import_json(args.source, args.output)
print(f"Imported {count} records into {args.output}")
