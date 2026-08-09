#!/usr/bin/env python3
"""Build the dependency-free AI Collab visible bridge VSIX."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def build(source: Path, output: Path) -> Path:
    manifest = json.loads((source / "package.json").read_text(encoding="utf-8"))
    package_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{manifest['publisher']}.{manifest['name']}" Version="{manifest['version']}" Publisher="{manifest['publisher']}" />
    <DisplayName>{manifest['displayName']}</DisplayName>
    <Description xml:space="preserve">{manifest['description']}</Description>
    <Tags>ai,collaboration,terminal</Tags>
    <Categories>Other</Categories>
    <Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="{manifest['engines']['vscode']}" /></Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code" /></Installation>
  <Dependencies />
  <Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" /></Assets>
</PackageManifest>
"""
    content_types = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="js" ContentType="application/javascript" />
  <Default Extension="vsixmanifest" ContentType="text/xml" />
</Types>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extension.vsixmanifest", package_xml)
        archive.writestr("[Content_Types].xml", content_types)
        archive.write(source / "package.json", "extension/package.json")
        archive.write(source / "extension.js", "extension/extension.js")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.source.resolve(), args.output.expanduser().resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
