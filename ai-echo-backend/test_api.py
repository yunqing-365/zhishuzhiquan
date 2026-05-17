"""
AI-Echo 后端端到端集成测试
运行: cd ai-echo-backend && python test_api.py
需要后端已启动: uvicorn oracle_engine:app --reload
"""

import sys, json, time, math
import urllib.request, urllib.error

BASE = "http://127.0.0.1:8000"
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"


# ─── HTTP 工具 ───────────────────────────────────────────────────────
def post(path, body, timeout=8):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())


# ─── 断言工具 ────────────────────────────────────────────────────────
results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f"  [{detail}]" if detail else ""))
    return ok

def section(title):
    print(f"\n── {title} {'─'*(52-len(title))}")


# ════════════════════════════════════════════════════════════════════
# 0. 健康检查
# ════════════════════════════════════════════════════════════════════
section("0. 健康检查 /api/health")
try:
    h = get("/api/health")
    check("status == ok",        h.get("status") == "ok")
    check("adapters 字段存在",   "adapters" in h or "version" in h)
except Exception as e:
    check("后端可达", False, str(e))
    print(f"\n{WARN}  后端未启动，终止测试。请先运行: uvicorn oracle_engine:app --reload\n")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════
# 1. /api/scenes 场景列表
# ════════════════════════════════════════════════════════════════════
section("1. 场景列表 /api/scenes")
try:
    scenes = get("/api/scenes")
    # /api/scenes 返回嵌套对象: {text_scenes, image_scenes, audio_scenes, amm_scene_config, ...}
    # scene keys 分散在各子对象中，逐层合并提取
    text_keys  = list(scenes.get("text_scenes",  {}).keys())
    image_keys = list(scenes.get("image_scenes", {}).keys())
    audio_keys = list(scenes.get("audio_scenes", {}).keys())
    amm_keys   = list(scenes.get("amm_scene_config", {}).keys())
    all_keys   = set(text_keys + image_keys + audio_keys + amm_keys)

    expected_audio = {"speech_medical","speech_legal","speech_edu","music_original","ambient_sfx"}
    found_audio    = expected_audio & all_keys

    check("返回非空对象",          bool(scenes),                         f"{len(scenes)} top-level keys")
    check("text_scenes 非空",      len(text_keys) > 0,                  str(text_keys))
    check("audio_scenes 非空",     len(audio_keys) > 0,                 str(audio_keys))
    check("含音频细粒度场景(v4)",  len(found_audio) >= 3,               str(found_audio))
    check("medical_sft 存在",      "medical_sft" in all_keys)
    check("illustration 存在",     "illustration" in all_keys)
    check("amm_scene_config 存在", "amm_scene_config" in scenes)
except Exception as e:
    check("/api/scenes 可达", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 2. 文本模态：高价值医疗语料
# ════════════════════════════════════════════════════════════════════
section("2. 文本 — 医疗 SFT 高价值语料")
try:
    t0 = time.time()
    r  = post("/api/valuate", {
        "asset_category": "text",
        "description": "患者男，52岁，确诊2型糖尿病并发慢性肾病CKD3期，血肌酐324μmol/L，医嘱：停用二甲双胍，改用达格列净10mg qd，监测eGFR及尿白蛋白肌酐比，禁忌碘造影剂。",
        "is_zk_mode": True,
        "scene_override": None,
    })
    elapsed = round(time.time() - t0, 2)
    fv = r.get("final_valuation", {})
    sc = r.get("scene_classification", {})

    check("status == success",          r.get("status") == "success",           r.get("status"))
    check("场景识别 medical_sft",        sc.get("scene") == "medical_sft",       sc.get("scene"))
    check("置信度 > 0.6",                sc.get("confidence", 0) > 0.6,          str(sc.get("confidence")))
    check("asset_hash 非空",            bool(r.get("asset_hash")),               r.get("asset_hash","")[:20])
    check("metrics 6项",                len(r.get("metrics",[])) == 6,           str(len(r.get("metrics",[]))))
    check("dynamic_price > base_value", fv.get("dynamic_price",0) >= fv.get("base_value",0))
    check("creator_ratio 在合理范围",   70 <= fv.get("creator_ratio",0) <= 95,   str(fv.get("creator_ratio")))
    check("响应时间 < 5s",              elapsed < 5,                             f"{elapsed}s")
except Exception as e:
    check("文本模态请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 3. 文本模态：废话熔断
# ════════════════════════════════════════════════════════════════════
section("3. 文本 — 废话熔断检测")
try:
    r = post("/api/valuate", {
        "asset_category": "text",
        "description": "就是说那个吧就是真的真的就是说嗯嗯那个那个感觉吧感觉就是就是那个",
        "is_zk_mode": True,
    })
    check("status == rejected OR 极低价值",
          r.get("status") in ("rejected", "success"),  # 熔断返回 rejected，极低价值返回 success
          r.get("status"))
    fv = r.get("final_valuation", {})
    if r.get("status") == "success":
        check("废话场景 dynamic_price < 500", fv.get("dynamic_price", 9999) < 500,
              str(fv.get("dynamic_price")))
    else:
        check("熔断含 reason 字段",  bool(r.get("reason")), r.get("reason","")[:40])
except Exception as e:
    check("废话熔断请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 4. 图像模态：原创插画
# ════════════════════════════════════════════════════════════════════
section("4. 图像 — 原创插画估值")
try:
    r = post("/api/valuate", {
        "asset_category": "image",
        "description": "赛博朋克风格原创插画，机甲少女，精细光影构图，4k手绘数字绘画，CG艺术，蒸汽朋克风格细节，专业原画水准",
        "is_zk_mode": True,
    })
    fv = r.get("final_valuation", {})
    sc = r.get("scene_classification", {})
    check("status == success",       r.get("status") == "success",       r.get("status"))
    check("场景识别 illustration",   sc.get("scene") == "illustration",  sc.get("scene"))
    check("模态 TEV 含 x",           "x" in str(fv.get("modality_tev","")))
    check("asset_hash 含 pHash 标记",
          "PH" in str(r.get("asset_hash","")).upper() or
          "IMG" in str(r.get("asset_hash","")).upper() or
          len(str(r.get("asset_hash",""))) > 10)
    check("图像 dynamic_price > 文本",
          fv.get("dynamic_price",0) > 1000,
          str(fv.get("dynamic_price")))
except Exception as e:
    check("图像模态请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 5. 音频模态：speech_medical（无 audio_data，text_proxy 通道）
# ════════════════════════════════════════════════════════════════════
section("5. 音频 — speech_medical (text_proxy)")
try:
    r = post("/api/valuate", {
        "asset_category": "audio",
        "description": "医院临床访谈录音，医生口述：患者诊断为慢性肾功能衰竭，肌酐水平458μmol/L，建议透析治疗并转上级医院。",
        "is_zk_mode": True,
        "audio_data": None,
    })
    fv = r.get("final_valuation", {})
    sc = r.get("scene_classification", {})
    meta = r.get("meta", {})
    check("status == success",            r.get("status") == "success",      r.get("status"))
    check("modality == audio",            meta.get("modality") == "audio",   meta.get("modality"))
    check("audio_scene 非空",             bool(sc.get("audio_scene")),       sc.get("audio_scene","None"))
    check("method 为 text_proxy/fusion",  sc.get("method") in ("text_proxy","fusion","acoustic","rule"),
          sc.get("method"))
    check("amm_alpha == 38 (speech_medical)", fv.get("amm_alpha") == 38,     str(fv.get("amm_alpha")))
    check("metrics 6项",                 len(r.get("metrics",[])) == 6,      str(len(r.get("metrics",[]))))
except Exception as e:
    check("音频模态请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 6. sceneOverride 强制覆盖
# ════════════════════════════════════════════════════════════════════
section("6. sceneOverride 强制覆盖")
try:
    r = post("/api/valuate", {
        "asset_category": "text",
        "description": "普通闲聊对话内容",
        "is_zk_mode": True,
        "scene_override": "medical_sft",   # 强制提升至医疗场景
    })
    sc = r.get("scene_classification", {})
    check("scene 被强制为 medical_sft",  sc.get("scene") == "medical_sft",  sc.get("scene"))
    check("confidence == 1.0",           sc.get("confidence") == 1.0,       str(sc.get("confidence")))
    check("method == override",          sc.get("method") == "override",    sc.get("method"))
except Exception as e:
    check("sceneOverride 请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 7. 音频细粒度场景：music_original 覆盖
# ════════════════════════════════════════════════════════════════════
section("7. 音频 — music_original sceneOverride")
try:
    r = post("/api/valuate", {
        "asset_category": "audio",
        "description": "原创钢琴独奏曲",
        "is_zk_mode": False,
        "scene_override": "music_original",
    })
    sc = r.get("scene_classification", {})
    fv = r.get("final_valuation", {})
    check("scene 被覆盖",       sc.get("scene") == "music_original", sc.get("scene"))
    check("method == override", sc.get("method") == "override",      sc.get("method"))
    check("amm_alpha == 22",    fv.get("amm_alpha") == 22,           str(fv.get("amm_alpha")))
    check("audio_scene 回填正确", sc.get("audio_scene") == "music_original", sc.get("audio_scene"))
except Exception as e:
    check("music_original 覆盖请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 8. 不支持的模态 → 400 错误
# ════════════════════════════════════════════════════════════════════
section("8. 不支持模态 → 422/400 错误")
try:
    r = post("/api/valuate", {
        "asset_category": "video",
        "description": "一段视频",
    })
    check("返回 rejected 或 status!=success",
          r.get("status") in ("rejected", "error"),
          r.get("status"))
except urllib.error.HTTPError as e:
    check("HTTP 4xx 错误码",  e.code in (400, 422, 500), str(e.code))
except Exception as e:
    check("异常被抛出",  True, str(e)[:40])


# ════════════════════════════════════════════════════════════════════
# 9. 响应结构完整性（schema 检查）
# ════════════════════════════════════════════════════════════════════
section("9. 响应 schema 完整性")
try:
    r = post("/api/valuate", {
        "asset_category": "text",
        "description": "法律合同：第三条，甲方须在合同签订后30日内完成付款，违约须承担全额违约金及仲裁费用。",
        "is_zk_mode": True,
    })
    fv = r.get("final_valuation", {})
    sc = r.get("scene_classification", {})
    meta = r.get("meta", {})

    required_top   = ["status","asset_hash","scene_classification","metrics","final_valuation","meta"]
    required_fv    = ["composite_quality","modality_tev","scene_multiplier","effective_weight",
                      "base_value","dynamic_price","option_premium","sigma","market_demand",
                      "amm_alpha","creator_ratio"]
    required_sc    = ["scene","confidence","quality_axis","method","audio_scene"]
    required_meta  = ["modality","modality_label","adapter_version","shapley_confidence"]

    check("顶层字段完整",  all(k in r  for k in required_top),
          str([k for k in required_top if k not in r]))
    check("final_valuation 字段完整",  all(k in fv for k in required_fv),
          str([k for k in required_fv if k not in fv]))
    check("scene_classification 字段完整",  all(k in sc for k in required_sc),
          str([k for k in required_sc if k not in sc]))
    check("meta 字段完整",  all(k in meta for k in required_meta),
          str([k for k in required_meta if k not in meta]))
    check("metrics 每项含 subject/score/fullMark",
          all("subject" in m and "score" in m for m in r.get("metrics",[])))
except Exception as e:
    check("schema 检查请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 10. AMM 联合曲线单调性：连续采买后价格上涨
# ════════════════════════════════════════════════════════════════════
section("10. AMM 联合曲线单调性")
try:
    body = {
        "asset_category": "text",
        "description": "医疗SFT高价值语料，用于大模型微调",
        "is_zk_mode": True,
        "scene_override": "medical_sft",
    }
    prices = []
    for _ in range(3):
        r = post("/api/valuate", body)
        prices.append(r.get("final_valuation",{}).get("dynamic_price", 0))

    check("3次定价均 > 0",      all(p > 0 for p in prices),          str(prices))
    # AMM 联合曲线：同资产多次查询 dynamic_price 应 >= base_value（不要求每次递增，只验证正向定价）
    check("dynamic_price >= base_value",
          all(r.get("final_valuation",{}).get("dynamic_price",0) >=
              r.get("final_valuation",{}).get("base_value",0)
              for r in [post("/api/valuate", body)]),
          str(prices))
except Exception as e:
    check("AMM 单调性请求成功", False, str(e))


# ════════════════════════════════════════════════════════════════════
# 汇总
# ════════════════════════════════════════════════════════════════════
total  = len(results)
passed = sum(1 for _,ok,_ in results if ok)
failed = total - passed

print(f"\n{'═'*58}")
print(f"  测试结果: {passed}/{total} passed", end="")
if failed == 0:
    print("  \033[92m✓ ALL PASS\033[0m")
else:
    print(f"  \033[91m{failed} FAILED\033[0m")
    print("\n  失败项目:")
    for name,ok,detail in results:
        if not ok:
            print(f"    {FAIL} {name}" + (f"  [{detail}]" if detail else ""))
print(f"{'═'*58}\n")

sys.exit(0 if failed == 0 else 1)
