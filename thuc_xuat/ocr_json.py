"""Decode model responses that contain prose, code fences or multiple JSON blocks."""
import json
import re


class InvalidOcrResponse(ValueError):
  """The model did not return a complete result of the expected shape."""


# Look for JSON objects and arrays, ignoring ordinary brackets in prose.
_START = re.compile(r'\{(?=\s*["}])|\[(?=\s*[\d"\[\]{}-]|\s*(?:true|false|null)\b)')


def json_blocks(text):
  """Decode complete top-level values without relying on matching fence markers."""
  decoder = json.JSONDecoder()
  blocks = []
  cursor = 0
  while start := _START.search(text, cursor):
    try:
      block, cursor = decoder.raw_decode(text, start.start())
    except (ValueError, RecursionError) as exc:
      raise InvalidOcrResponse("NIM trả một khối JSON bị lỗi hoặc bị cắt.") from exc
    blocks.append(block)
  if not blocks:
    raise InvalidOcrResponse("NIM không trả dữ liệu JSON đọc được.")
  return blocks


def read_parsed(nim, prompt, images, parse, cancel_event, on_progress):
  """Give an invalid model response one fresh attempt, preserving API errors."""
  try:
    return parse(nim.read(prompt, images, cancel_event, on_progress))
  except InvalidOcrResponse:
    if on_progress:
      on_progress("Đang đọc lại vì phản hồi NIM chưa đủ hoặc không đúng định dạng.")
  retry_prompt = prompt + "\nChỉ trả một đối tượng JSON hoàn chỉnh theo mẫu, không lặp kết quả."
  return parse(nim.read(retry_prompt, images, cancel_event, on_progress))
