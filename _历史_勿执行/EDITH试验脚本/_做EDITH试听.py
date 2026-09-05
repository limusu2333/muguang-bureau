#!/usr/bin/env python3
"""EDITH 声音克隆+试听:用 OSS 上的两段录音各克隆一个声线,各合成一句总结口吻的话,存下来给船主对比挑。"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 模型接入 import _加载_env

_加载_env()
import dashscope

key = os.environ["EDITH_DASHSCOPE_KEY"]  # EDITH 专用原生 key
dashscope.api_key = key
from dashscope.audio.tts_v2 import VoiceEnrollmentService, SpeechSynthesizer

COMPANY = Path(__file__).resolve().parents[1]
试听目录 = COMPANY / "运行状态" / "声线试听"
试听目录.mkdir(parents=True, exist_ok=True)

录音 = {
    "v1_44k": "https://edith-voice.oss-cn-beijing.aliyuncs.com/EDITH.wav",
    "v2_48k": "https://edith-voice.oss-cn-beijing.aliyuncs.com/EDITH2.wav",
}
试听词 = "长官，今天的项目汇报：开发公司七条债已经完成六条，记忆向量也通过了验证，随时听候你的指示。"

svc = VoiceEnrollmentService()
结果 = []
for 名, url in 录音.items():
    try:
        vid = svc.create_voice(target_model="cosyvoice-v2", prefix="edith", url=url)
        print(f"✓ {名} 克隆成功,voice_id = {vid}")
        # 合成(给一点点生效时间)
        time.sleep(1.0)
        syn = SpeechSynthesizer(model="cosyvoice-v2", voice=vid)
        audio = syn.call(试听词)
        out = 试听目录 / f"EDITH_{名}.mp3"
        with open(out, "wb") as f:
            f.write(audio)
        size = out.stat().st_size
        print(f"  合成试听 → {out}  ({size} 字节)")
        结果.append((名, vid, str(out), size))
    except Exception as e:
        print(f"✗ {名} 失败: {type(e).__name__}: {str(e)[:300]}")

print("\n=== 给船主挑 ===")
for 名, vid, path, size in 结果:
    print(f"  {名}: {path}  (voice_id={vid})")
if 结果:
    print("\n两个试听文件已生成,船主听了挑哪个像;挑定后我把那个 voice_id 固化、接进办公室让项目经理念。")
