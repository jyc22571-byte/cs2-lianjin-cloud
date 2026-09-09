# -*- coding: utf-8 -*-
"""云端每日扫描（GitHub Actions 用 · 纯标准库，无第三方依赖）

流程：读 数据/ 静态规则库 → SteamDT 批量接口拉价（100 个/批，接口限 1 次/分钟）
      → 用与本地 扫描服务.py 相同的算法产出三档 净EV榜.json。

EA 算法重构点：只需改本文件的 compute_board()，其余（取价/名单/结构）可不动。

环境：STEAMDT_API_KEY（GitHub Actions Secret）
产物：净EV榜.json  ← v2 EXE B 页“读取本地结果/云端同步”同构可直接读
"""
import os, sys, json, time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "数据")
KEY = os.environ.get("STEAMDT_API_KEY", "").strip()
FEE_BUY = 0.025   # 手续费口径须与本地 配置/settings.json 一致
FEE_SELL = 0.025
PLATFORM_ORDER = ["BUFF"]   # 本地默认同 BUFF
if not KEY:
    print("未设置 STEAMDT_API_KEY", file=sys.stderr)
    sys.exit(1)

# ---------- 静态数据 ----------
def load_json(name):
    return json.load(open(os.path.join(DATA, name), encoding="utf-8"))

SCC = load_json("skins_collections_containers.json")
POOLS = load_json("上级产物池.json")["collections"]
BRIDGE = load_json("规则库_v03.json")["skins"]

ITEM = {}          # item_id -> {zh, en, rarity, cols:[(cid,cname)], bridge}
for cid, cv in SCC["collections"].items():
    cname = cv.get("name_zh") or cv.get("name_en") or cid
    for s in cv["skins"]:
        it = ITEM.setdefault(s["item_id"], {"zh": None, "en": s.get("full_en"),
                                            "rarity": s.get("rarity"), "cols": []})
        it["cols"].append((cid, cname))
        it["zh"] = it["zh"] or s.get("full_zh")
for iid, b in BRIDGE.items():
    if iid in ITEM:
        it = ITEM[iid]
        it["zh"] = it["zh"] or b.get("zh_steamdt") or b.get("zh")
        it["bridge"] = b

def chain_of(iid):
    """(cid, 当前稀有度, 上级产物池[list{id,...}]) 或 None"""
    it = ITEM.get(iid)
    if not it:
        return None
    for cid, _ in it.get("cols", []):
        t = POOLS.get(cid, {}).get("tiers", {}).get(it["rarity"])
        if t and t.get("pool"):
            return cid, it["rarity"], t["pool"]
    return None

MAT_ITEMS = [(it["zh"] or it["en"], iid) for iid, it in ITEM.items()
             if it.get("bridge") and chain_of(iid)]   # 仅可炼（有上级产物池）

def mh_of(iid, wear):
    return ((ITEM.get(iid, {}).get("bridge", {}) or {}).get("market_by_wear", {}) or {}).get(wear)

# ---------- 取价名单（与本地 轻扫服务.build_need(False) 同口径，约4-5k 名） ----------
FILLER_CANDS = ["Five-SeveN | Desert Seal", "Nova | Morning Sun", "MP9 | Dune Asp",
                "PP-Bizon | Traitor", "Dual Berettas | Mystic Conjunction"]

def build_need():
    need = {}
    for cid, cent in POOLS.items():
        cv = SCC["collections"].get(cid)
        if not cv:
            continue
        for r, tt in (cent or {}).get("tiers", {}).items():
            pool = tt.get("pool")
            if not pool:
                continue
            # 主料档：FT/MW/WW
            for s in [x for x in cv["skins"] if x.get("rarity") == r]:
                for wm in ("Field-Tested", "Minimal Wear", "Well-Worn"):
                    mh = mh_of(s["item_id"], wm)
                    if mh:
                        need[mh] = True
            # 本链产物档：FN/MW/FT/WW（WW 供 S破损 的同磨损初筛）
            for p in pool:
                for wt in ("Factory New", "Minimal Wear", "Field-Tested", "Well-Worn"):
                    mh = mh_of(p["id"], wt)
                    if mh:
                        need[mh] = True
    # 通用辅料（FN）及其链产物档
    for cand in FILLER_CANDS:
        for _, i_ in MAT_ITEMS:
            if (ITEM[i_]["en"] or "").lower() == cand.lower():
                mh = mh_of(i_, "Factory New")
                if mh:
                    need[mh] = True
                ch = chain_of(i_)
                if ch:
                    for p in ch[2]:
                        for wt in ("Factory New", "Minimal Wear", "Field-Tested"):
                            mh = mh_of(p["id"], wt)
                            if mh:
                                need[mh] = True
                break
    return sorted(need)

# ---------- 批量取价 ----------
PRICES = {}   # marketHashName -> {platform: entry}

def batch_fetch(names):
    body = json.dumps({"marketHashNames": names}).encode()
    req = urllib.request.Request("https://open.steamdt.com/open/cs2/v1/price/batch",
                                 data=body, method="POST",
                                 headers={"Authorization": "Bearer " + KEY,
                                          "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode("utf-8"))
    got = 0
    for x in res.get("data") or []:
        mh = x.get("marketHashName")
        if not mh:
            continue
        pl = {}
        for p in x.get("dataList") or []:
            if isinstance(p, dict):
                pl[p.get("platform")] = p
        PRICES[mh] = pl
        got += 1
    return got

def run_prices(names):
    total = len(names)
    if total == 0:
        print("名单为空", file=sys.stderr)
        sys.exit(2)
    for i in range(0, total, 100):
        chunk = names[i:i + 100]
        for attempt in range(4):
            try:
                got = batch_fetch(chunk)
                print(f"取价 {min(i + 100, total)}/{total}（本批返回 {got}）", flush=True)
                break
            except Exception as e:
                if attempt == 3:
                    print(f"批次 {i // 100 + 1} 连续失败，跳过: {e}", flush=True)
                else:
                    print(f"批次 {i // 100 + 1} 失败重试 {attempt + 1}: {e}", flush=True)
                    time.sleep(20 * (attempt + 1))
        if i + 100 < total:
            time.sleep(61)   # 批量接口限 1 次/分钟

def price_of(mh):
    if not mh:
        return None
    pl = PRICES.get(mh)
    if not pl:
        return None
    for name in PLATFORM_ORDER:
        p = pl.get(name)
        if p and p.get("sellPrice"):
            return p["sellPrice"]
    return None

# ---------- 算榜（EA 简化版占位；重构改这里） ----------
MODES = [("久经→略磨", "Field-Tested", "Minimal Wear"),
         ("略磨→崭新", "Minimal Wear", "Factory New"),
         ("破损→久经", "Well-Worn", "Field-Tested")]

def pick_filler():
    """通用辅料：候选里 FN 价最低的一个可炼皮肤"""
    best = None
    for cand in FILLER_CANDS:
        for _, i_ in MAT_ITEMS:
            if (ITEM[i_]["en"] or "").lower() == cand.lower():
                c = price_of(mh_of(i_, "Factory New"))
                if c and (best is None or c < best[1]):
                    best = (i_, c)
                break
    return best

def compute_board():
    f = pick_filler()
    if f is None:
        print("取不到通用辅料价格", file=sys.stderr)
        return None
    f_iid, f_cost = f
    fch = chain_of(f_iid)
    results = {m[0]: [] for m in MODES}
    for mode, wear_m, wear_t in MODES:
        for cid, cv in SCC["collections"].items():
            tiers = POOLS.get(cid, {}).get("tiers", {})
            for r, tt in tiers.items():
                pool = tt.get("pool")
                if not pool:
                    continue
                # 3 个最便宜主料（买 主料档）
                best = None
                for s in [x for x in cv["skins"] if x.get("rarity") == r]:
                    c = price_of(mh_of(s["item_id"], wear_m))
                    if c and (best is None or c < best[1]):
                        best = (s["item_id"], c)
                if best is None:
                    continue
                mid, mcost = best
                # 主链产物 均价（卖 产物档）
                pv = [price_of(mh_of(p["id"], wear_t)) for p in pool]
                pv = [v for v in pv if v]
                if not pv:
                    continue
                pA = sum(pv) / len(pv)
                # 辅料链产物 均价
                fpv = []
                if fch:
                    for p in fch[2]:
                        v = price_of(mh_of(p["id"], wear_t))
                        if v:
                            fpv.append(v)
                pF = sum(fpv) / len(fpv) if fpv else 0.0
                cost = (3 * mcost + 7 * f_cost) * (1 + FEE_BUY)
                exp = 0.3 * pA + 0.7 * pF
                ev = exp * (1 - FEE_SELL) - cost
                results[mode].append({
                    "col": cv.get("name_zh") or cv.get("name_en") or cid,
                    "main": ITEM[mid].get("zh") or ITEM[mid]["en"],
                    "iid": mid, "cost": round(cost, 2), "ev": round(ev, 2),
                    "roi": round(100 * ev / cost, 2) if cost else 0,
                    "chain": f"{r}→{tt['pool_tier']}"})
    for m in results:
        results[m].sort(key=lambda x: -x["ev"])
    return {"date": time.strftime("%Y-%m-%d %H:%M"), "modes": results}

# ---------- 主料 S 榜：初筛(同磨损≥0.8)后按 S 排序 ----------
S_LABELS = [("S破损", "Well-Worn"), ("S酒精", "Field-Tested"), ("S略磨", "Minimal Wear")]
WEAR_LOW  = {"Well-Worn": "Field-Tested", "Field-Tested": "Minimal Wear", "Minimal Wear": "Factory New"}

def pool_mean(iid, wear_en):
    """主料 iid 的上级产物池在 wear_en 档的均价（无价返回 None）"""
    ch = chain_of(iid)
    if not ch:
        return None
    vals = []
    for p in ch[2]:
        c = price_of(mh_of(p["id"], wear_en))
        if c:
            vals.append(c)
    if not vals:
        return None
    return sum(vals) / len(vals)

def compute_mains():
    modes = {lb: [] for lb, _ in S_LABELS}
    for cid, cv in SCC["collections"].items():
        tiers = POOLS.get(cid, {}).get("tiers", {})
        nm = cv.get("name_zh") or cv.get("name_en") or cid
        for r, tt in tiers.items():
            if not tt.get("pool"):
                continue
            for s in [x for x in cv["skins"] if x.get("rarity") == r]:
                # 区间太小（如仅崭新/窄区间）当不了高磨损主料 → 剔除
                span = (s.get("wear_max") or 0) - (s.get("wear_min") or 0)
                if span < 0.7:
                    continue
                iid = s["item_id"]
                for lb, buy in S_LABELS:
                    p = price_of(mh_of(iid, buy))
                    if not p:
                        continue
                    same = pool_mean(iid, buy)      # 同磨损（初筛）
                    low  = pool_mean(iid, WEAR_LOW[buy])   # 低磨损产物（S 分子）
                    if not same or not low:
                        continue
                    fv = same / 10.0 / p            # 初筛值
                    sv = low / 10.0 / p              # S
                    if fv >= 0.8:                    # 初筛门槛
                        modes[lb].append({
                            "iid": iid,
                            "name": ITEM.get(iid, {}).get("zh") or ITEM.get(iid, {}).get("en") or "",
                            "col": nm, "rarity": r,
                            "span": round(span, 4),
                            "main_price": round(p, 2),
                            "filter_v": round(fv, 4), "S": round(sv, 4)})
    for lb in modes:
        modes[lb].sort(key=lambda x: -x["S"])
    return {"date": time.strftime("%Y-%m-%d %H:%M"), "modes": modes}

# ---------- 主流程 ----------
def main():
    t0 = time.time()
    names = build_need()
    print(f"待取名 {len(names)} 个，约 {len(names) // 100 + 1} 批", flush=True)
    run_prices(names)
    board = compute_board()
    if board is None:
        sys.exit(3)
    dest = os.path.join(BASE, "净EV榜.json")
    json.dump(board, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for m in board["modes"]:
        top = board["modes"][m][0]
        print(f"[{m}] 榜首 {top['main']} 净EV {top['ev']} 收益 {top['roi']}%", flush=True)
    mains = compute_mains()
    mdest = os.path.join(BASE, "主料榜.json")
    json.dump(mains, open(mdest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for lb in mains["modes"]:
        rows = mains["modes"][lb]
        print(f"[{lb}] 通过初筛 {len(rows)}，榜首 S={rows[0]['S'] if rows else '-'} {rows[0]['name'] if rows else ''}", flush=True)
    print(f"saved {dest} + {mdest} · elapsed {int(time.time() - t0)}s", flush=True)

if __name__ == "__main__":
    main()
