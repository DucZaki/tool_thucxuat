# -*- coding: utf-8 -*-
"""Lấy mã PDN từ tên file Excel / ảnh phiếu."""
from __future__ import annotations

import re
from pathlib import Path

PDN_RE = re.compile(r"PDN\d+", re.IGNORECASE)


def extract_pdn(text: str) -> str | None:
  m = PDN_RE.search(text or "")
  return m.group(0).upper() if m else None


def pdn_from_excel(path: Path) -> str | None:
  return extract_pdn(path.name)
