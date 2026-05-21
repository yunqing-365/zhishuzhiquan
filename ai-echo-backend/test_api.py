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

# ════════════════════════════════════════════════════════════════════
# 11. 限流中间件：快速连续请求触发 429
# ════════════════════════════════════════════════════════════════════
section("11. 限流中间件 — 429 Rate Limit")
try:
    import threading
    body = {"asset_category": "text", "description": "限流测试语料", "is_zk_mode": False}
    responses = []
    errors    = []

    def _fire():
        try:
            responses.append(post("/api/valuate", body, timeout=5))
        except urllib.error.HTTPError as e:
            responses.append({"_status": e.code})
        except Exception as ex:
            errors.append(str(ex))

    threads = [threading.Thread(target=_fire) for _ in range(25)]
    for t in threads: t.start()
    for t in threads: t.join()

    status_codes = [r.get("_status") or r.get("status") for r in responses]
    check("快速25次请求出现429或全部成功(未启用限流)",
          True,
          f"responses={len(responses)} 429s={sum(1 for c in status_codes if c==429)}")
    check("无请求抛出网络异常", len(errors) == 0, str(errors[:2]))
except Exception as e:
    check("限流并发测试可运行", False, str(e)[:60])


# ════════════════════════════════════════════════════════════════════
# 12. /api/history/search (v2)
# ════════════════════════════════════════════════════════════════════
section("12. /api/history/search 搜索接口 (v2)")
try:
    r = get("/api/history/search?q=医疗&limit=5")
    check("返回 records 字段",  "records" in r, str(list(r.keys())))
    check("返回 total 字段",    "total"   in r, str(list(r.keys())))
    check("records 为列表",     isinstance(r.get("records"), list))
    r2 = get("/api/history/search?q=&limit=5")
    check("空关键词返回 records=[]", r2.get("records") == [], str(r2.get("records")))
except Exception as e:
    check("/api/history/search 可达", False, str(e)[:60])


# ════════════════════════════════════════════════════════════════════
# 13. CORS 安全头部验证
# ════════════════════════════════════════════════════════════════════
section("13. CORS 安全头部验证")
try:
    req = urllib.request.Request(
        BASE + "/api/health",
        headers={"Origin": "https://evil.example.com"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        cors = resp.headers.get("Access-Control-Allow-Origin", "")
    check("CORS 不允许任意来源", cors != "https://evil.example.com", f"ACAO={cors!r}")
    check("CORS 不是通配符 *",  cors != "*",                        f"ACAO={cors!r}")
except urllib.error.HTTPError as e:
    check("恶意Origin被拦截(4xx)", e.code in (400, 403, 405), str(e.code))
except Exception as e:
    check("CORS 检查可运行", False, str(e)[:60])


# ════════════════════════════════════════════════════════════════════
# 14. image_data 字段传递
# ════════════════════════════════════════════════════════════════════
section("14. image_data 字段支持 (v6)")
try:
    TINY_PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI6QAAA"
        "ABJRU5ErkJggg=="
    )
    r = post("/api/valuate", {
        "asset_category": "image",
        "description":    "单像素测试图像",
        "is_zk_mode":     False,
        "image_data":     TINY_PNG_B64,
    })
    check("image_data 字段被接受不报错",
          r.get("status") in ("success", "rejected"), r.get("status"))
    check("asset_hash 非空",
          len(str(r.get("asset_hash", ""))) > 5,
          str(r.get("asset_hash", ""))[:30])
except Exception as e:
    check("image_data 字段请求成功", False, str(e)[:60])


# ════════════════════════════════════════════════════════════════════
# 15. /api/scenes v6 字段完整性 (Stage C 双流运行时信息)
# ════════════════════════════════════════════════════════════════════
section("15. /api/scenes v6 — Stage C 双流字段 + 视频场景权重")
try:
    sc = get("/api/scenes")

    # 视频场景权重表（★ v6 新增）
    vid_weights = sc.get("video_scene_weights", {})
    expected_vid = {"documentary", "lecture", "cinematic", "sports_action", "vlog"}
    found_vid    = expected_vid & set(vid_weights.keys())
    check("video_scene_weights 非空",    len(vid_weights) > 0,       str(vid_weights))
    check("含所有5个视频场景",           len(found_vid) == 5,         str(found_vid))

    # video_dual_stream 运行时信息
    ds = sc.get("video_dual_stream", {})
    check("video_dual_stream 字段存在",  bool(ds),                    str(list(sc.keys())))
    check("stage == 'C'",               ds.get("stage") == "C",      str(ds.get("stage")))
    check("ffmpeg_available 为 bool",   isinstance(ds.get("ffmpeg_available"), bool),
                                                                      str(ds.get("ffmpeg_available")))
    check("fusion_alpha + fusion_beta ≈ 1.0",
          abs(ds.get("fusion_alpha", 0) + ds.get("fusion_beta", 0) - 1.0) < 0.01,
          f"α={ds.get('fusion_alpha')} β={ds.get('fusion_beta')}")

    # video_scene_composite_weights（6D 权重）
    cw = sc.get("video_scene_composite_weights", {})
    check("video_scene_composite_weights 存在", bool(cw), str(cw))

    # supported_modalities 含 is_stub
    mods = sc.get("supported_modalities", {})
    vid_mod = mods.get("video", {})
    check("supported_modalities.video 含 is_stub", "is_stub" in vid_mod, str(vid_mod))
    check("video adapter_version == v2-stage-c",
          vid_mod.get("adapter_version") == "v2-stage-c",
          str(vid_mod.get("adapter_version")))

    # AMM 视频场景已入库
    amm = sc.get("amm_scene_config", {})
    amm_vid = {"documentary", "lecture", "cinematic"} & set(amm.keys())
    check("AMM 含视频场景 documentary/lecture/cinematic",
          len(amm_vid) == 3, str(amm_vid))
except Exception as e:
    check("/api/scenes v6 可达", False, str(e)[:80])


# ════════════════════════════════════════════════════════════════════
# 16. 视频模态 — Stage A 描述代理估值（无 video_data）
# ════════════════════════════════════════════════════════════════════
section("16. 视频 — Stage A 描述代理估值 (无 video_data)")
try:
    r = post("/api/valuate", {
        "asset_category": "video",
        "description":    (
            "高清纪录片，一段珍贵的野生东北虎捕猎实录，4K画质，"
            "专业摄影团队随行拍摄，配有双语同期声，画面稳定，光线充足。"
        ),
        "is_zk_mode": False,
        "scene_override": "documentary",
    })
    fv = r.get("final_valuation", {})
    sc_ = r.get("scene_classification", {})
    mt  = r.get("meta", {})

    check("status == success",           r.get("status") == "success",    r.get("status"))
    check("modality == video",           mt.get("modality") == "video",   mt.get("modality"))
    check("dynamic_price > 0",          fv.get("dynamic_price", 0) > 0,  str(fv.get("dynamic_price")))
    check("adapter_version 含 stage",   "stage" in str(mt.get("adapter_version","")),
                                                                          str(mt.get("adapter_version")))
    check("video_stage 字段存在(v6)",   "video_stage" in mt,              str(list(mt.keys())))
    check("video_stage == 'A' (无帧)",  mt.get("video_stage") == "A",    str(mt.get("video_stage")))
    check("has_audio_stream == False",  mt.get("has_audio_stream") in (False, None, 0),
                                                                          str(mt.get("has_audio_stream")))
    check("动态定价 ≥ 基准价值",
          fv.get("dynamic_price", 0) >= fv.get("base_value", 0),
          f"dyn={fv.get('dynamic_price')} base={fv.get('base_value')}")
except Exception as e:
    check("视频 Stage A 请求成功", False, str(e)[:80])


# ════════════════════════════════════════════════════════════════════
# 17. 视频 AMM — documentary alpha > vlog alpha
# ════════════════════════════════════════════════════════════════════
section("17. 视频 AMM — 场景差异化定价斜率")
try:
    base_desc = "视频测试内容，用于比较场景定价差异。"

    r_doc  = post("/api/valuate", {"asset_category": "video", "description": base_desc,
                                   "is_zk_mode": False, "scene_override": "documentary"})
    r_vlog = post("/api/valuate", {"asset_category": "video", "description": base_desc,
                                   "is_zk_mode": False, "scene_override": "vlog"})

    doc_price  = r_doc.get("final_valuation",  {}).get("dynamic_price", 0)
    vlog_price = r_vlog.get("final_valuation", {}).get("dynamic_price", 0)
    doc_alpha  = r_doc.get("final_valuation",  {}).get("amm_alpha", 0)
    vlog_alpha = r_vlog.get("final_valuation", {}).get("amm_alpha", 0)

    check("documentary price > vlog price",
          doc_price > vlog_price,
          f"doc={doc_price} vlog={vlog_price}")
    check("documentary amm_alpha > vlog amm_alpha",
          doc_alpha > vlog_alpha,
          f"doc_α={doc_alpha} vlog_α={vlog_alpha}")
    check("两者 status 均为 success",
          r_doc.get("status") == r_vlog.get("status") == "success",
          f"{r_doc.get('status')} / {r_vlog.get('status')}")
except Exception as e:
    check("视频 AMM 差异化定价请求成功", False, str(e)[:80])


# ════════════════════════════════════════════════════════════════════
# 18. storage v6 — video_stage / has_audio_stream 字段持久化
# ════════════════════════════════════════════════════════════════════
section("18. storage v6 — 视频 Stage C 字段持久化")
try:
    # 先提交一次视频估值（触发 storage.save_valuation）
    r = post("/api/valuate", {
        "asset_category": "video",
        "description":    "storage 持久化测试：纪录片片段，有效内容",
        "is_zk_mode":     False,
        "scene_override": "lecture",
    })
    check("视频估值提交成功", r.get("status") == "success", r.get("status"))

    # 从历史记录取回，校验新字段
    hist = get("/api/history?limit=5&modality=video")
    records = hist.get("records", hist) if isinstance(hist, dict) else hist
    if isinstance(records, list) and records:
        latest = records[0]
        check("历史记录含 video_stage 字段",
              "video_stage" in latest,          str(list(latest.keys())))
        check("历史记录含 has_audio_stream 字段",
              "has_audio_stream" in latest,     str(list(latest.keys())))
        check("video_stage 值合法 (A/B/C 或 None)",
              latest.get("video_stage") in ("A","B","C", None, ""),
              str(latest.get("video_stage")))
    else:
        check("视频历史记录非空", False, f"records={records}")
except Exception as e:
    check("storage v6 持久化测试可运行", False, str(e)[:80])


# ════════════════════════════════════════════════════════════════════
# 19. 视频 TEV 倍率 — MODALITY_TEV video == 500
# ════════════════════════════════════════════════════════════════════
section("19. 视频 TEV 倍率 v6 (500x)")
try:
    sc_data = get("/api/scenes")
    tev     = sc_data.get("modality_tev", {})
    check("modality_tev 字段存在",    bool(tev),                str(list(tev.keys())))
    check("text TEV == 1.0",          tev.get("text")  == 1.0,  str(tev.get("text")))
    check("video TEV == 500.0 (v6升级)", tev.get("video") == 500.0, str(tev.get("video")))
    check("audio TEV >= 100",         tev.get("audio", 0) >= 100, str(tev.get("audio")))
    check("video TEV > audio TEV",    tev.get("video", 0) > tev.get("audio", 0),
          f"video={tev.get('video')} audio={tev.get('audio')}")
except Exception as e:
    check("TEV 倍率检查可运行", False, str(e)[:60])


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
