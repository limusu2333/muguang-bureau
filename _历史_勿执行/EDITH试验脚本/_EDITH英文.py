#!/usr/bin/env python3
"""用已克隆的两个 EDITH 声线,各合成一句英文试听(复用 voice_id,不重新克隆)。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 模型接入 import _加载_env

_加载_env()
import dashscope

dashscope.api_key = os.environ["EDITH_DASHSCOPE_KEY"]
from dashscope.audio.tts_v2 import SpeechSynthesizer

COMPANY = Path(__file__).resolve().parents[1]
试听目录 = COMPANY / "运行状态" / "声线试听"

voices = {
    "v1_44k": "cosyvoice-v2-edith-0b422706afe94ed29aae4f16d90ebf2c",
    "v2_48k": "cosyvoice-v2-edith-4a8eafd6f6bb41928b4060c7301cbcc4",
}
en = ("Good evening, sir. Here is today's report: six of the seven tasks are complete, "
      "and the memory system has passed verification. Standing by for your orders.")

for 名, vid in voices.items():
    try:
        syn = SpeechSynthesizer(model="cosyvoice-v2", voice=vid)
        audio = syn.call(en)
        out = 试听目录 / f"EDITH_{名}_en.mp3"
        with open(out, "wb") as f:
            f.write(audio)
        print(f"✓ {名} 英文合成 → {out}  ({out.stat().st_size} 字节)")
    except Exception as e:
        print(f"✗ {名} 英文失败: {type(e).__name__}: {str(e)[:300]}")
