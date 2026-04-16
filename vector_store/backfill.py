"""
vector_store/backfill.py
回填脚本：将已有项目中的 Bible、人物、伏笔等数据批量向量化

用法：
    # 回填所有项目
    python vector_store/backfill.py
    
    # 回填指定项目
    python vector_store/backfill.py --project-id 395dcf64-1e8c-4a63-b57b-a743c0744646
    
    # 只回填特定类型
    python vector_store/backfill.py --type bible,characters
"""

import sys
import asyncio
import argparse
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from vector_store.store import VectorStore
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB


async def backfill_bible(project_id: str, store: VectorStore) -> int:
    """回填 Bible 数据"""
    print(f"\n--- 回填 Bible ---")
    
    bible_db = BibleDB(project_id)
    
    # 主 Bible 文件
    bible_data = bible_db.load("bible")
    # 世界规则文件
    world_rules_data = bible_db.load("world_rules")
    
    if not bible_data and not world_rules_data:
        print("  [跳过] 未找到 Bible 数据")
        return 0
    
    items = []
    
    # 世界基本信息
    if bible_data:
        items.append({
            "id": "bible_world_info",
            "text": f"世界名称：{bible_data.get('world_name', '')}，世界描述：{bible_data.get('world_description', '')}",
            "metadata": {"type": "world_info"}
        })
        
        # 战力体系
        combat_system = bible_data.get("combat_system", {})
        if combat_system:
            realms = combat_system.get("realms", [])
            items.append({
                "id": "bible_combat_system",
                "text": f"战力体系名称：{combat_system.get('name', '')}，境界划分：{' → '.join(realms)}",
                "metadata": {"type": "combat_system"}
            })
            
            # 每个境界
            for i, realm in enumerate(realms):
                items.append({
                    "id": f"bible_realm_{i}",
                    "text": f"境界：{realm}",
                    "metadata": {"type": "realm", "index": i}
                })
        
        # 势力
        factions = bible_data.get("factions", [])
        for i, faction in enumerate(factions[:10]):
            items.append({
                "id": f"bible_faction_{i}",
                "text": f"势力名称：{faction.get('name', '')}，势力描述：{faction.get('description', '')}",
                "metadata": {"type": "faction", "index": i}
            })
        
        # 地理
        geography = bible_data.get("geography", {})
        # geography 可能是 dict（含 major_regions）或 list
        if isinstance(geography, dict):
            regions = geography.get("major_regions", [])
            for i, region in enumerate(regions[:10]):
                locations = "、".join(region.get("important_locations", []))
                items.append({
                    "id": f"bible_geography_{i}",
                    "text": f"区域：{region.get('name', '')}，描述：{region.get('description', '')}，重要地点：{locations}",
                    "metadata": {"type": "geography", "index": i}
                })
        elif isinstance(geography, list):
            for i, geo in enumerate(geography[:10]):
                items.append({
                    "id": f"bible_geography_{i}",
                    "text": f"地点：{geo.get('name', '')}，描述：{geo.get('description', '')}",
                    "metadata": {"type": "geography", "index": i}
                })
        
        # 文化
        culture = bible_data.get("culture", {})
        if culture:
            items.append({
                "id": "bible_culture",
                "text": f"文化设定：宗教{ culture.get('religion', '')}，禁忌{ culture.get('taboos', '')}，礼仪{ culture.get('customs', '')}",
                "metadata": {"type": "culture"}
            })
        
        # 历史笔记
        history = bible_data.get("history_notes", [])
        for i, note in enumerate(history):
            items.append({
                "id": f"bible_history_{i}",
                "text": f"历史：{note}",
                "metadata": {"type": "history_note", "index": i}
            })
    
    # 世界规则（来自 world_rules.json 或 bible_data 内的 world_rules）
    if world_rules_data:
        rules = world_rules_data.get("items", [])
        for i, rule in enumerate(rules):
            items.append({
                "id": f"bible_rule_{i}",
                "text": f"规则：{rule.get('description', '')}，分类：{rule.get('category', '')}",
                "metadata": {"type": "world_rule", "category": rule.get("category", "")}
            })
    elif bible_data:
        # bible_data 内可能直接包含 world_rules
        rules = bible_data.get("world_rules", [])
        if isinstance(rules, list):
            for i, rule in enumerate(rules):
                content_text = rule.get("content", rule.get("description", ""))
                category = rule.get("category", "")
                items.append({
                    "id": f"bible_rule_{i}",
                    "text": f"规则：{content_text}，分类：{category}，重要性：{rule.get('importance', '')}",
                    "metadata": {"type": "world_rule", "category": category}
                })
    
    if items:
        success = await store.upsert_batch(collection="bible_rules", items=items)
        print(f"  [OK] Bible 回填完成: {len(items)} 条")
        return len(items) if success else 0
    
    return 0


async def backfill_characters(project_id: str, store: VectorStore) -> int:
    """回填人物数据"""
    print(f"\n--- 回填人物 ---")
    
    char_db = CharacterDB(project_id)
    all_chars = char_db.list_all_characters()
    
    if not all_chars:
        print("  [跳过] 未找到人物数据")
        return 0
    
    items = []
    
    for char in all_chars:
        # 跳过无效数据
        if not char or not isinstance(char, dict):
            continue
        
        name = char.get("name", "未知")
        text_parts = [f"人物姓名：{name}"]
        
        if char.get("aliases"):
            text_parts.append(f"别名：{', '.join(char.get('aliases', []))}")
        if char.get("appearance"):
            text_parts.append(f"外貌：{char.get('appearance', '')}")
        if char.get("personality_core"):
            text_parts.append(f"性格：{char.get('personality_core', '')}")
        if char.get("background"):
            text_parts.append(f"背景：{char.get('background', '')}")
        if char.get("voice_keywords"):
            text_parts.append(f"说话特征：{', '.join(char.get('voice_keywords', []))}")
        
        cultivation = char.get("cultivation", {})
        if cultivation:
            text_parts.append(f"修为：{cultivation.get('realm', '')} {cultivation.get('stage', '')}")
        
        items.append({
            "id": f"char_{char.get('id', name)}",
            "text": "，".join(text_parts),
            "metadata": {
                "type": "character",
                "name": name,
                "role": "protagonist" if char.get("id") == "protagonist" else "supporting"
            }
        })
    
    if items:
        success = await store.upsert_batch(collection="character_cards", items=items)
        print(f"  [OK] 人物回填完成: {len(items)} 条")
        return len(items) if success else 0
    
    return 0


async def backfill_foreshadowing(project_id: str, store: VectorStore) -> int:
    """回填伏笔数据"""
    print(f"\n--- 回填伏笔 ---")
    
    try:
        fs_db = ForeshadowingDB(project_id)
        all_fs = fs_db.get_all() if hasattr(fs_db, 'get_all') else []
    except Exception:
        print("  [跳过] 伏笔库不存在或无数据")
        return 0
    
    if not all_fs:
        print("  [跳过] 未找到伏笔数据")
        return 0
    
    items = []
    
    for i, fs in enumerate(all_fs):
        if not fs or not isinstance(fs, dict):
            continue
        
        text_parts = []
        if fs.get("description"):
            text_parts.append(f"伏笔描述：{fs.get('description', '')}")
        if fs.get("trigger_condition"):
            text_parts.append(f"触发条件：{fs.get('trigger_condition', '')}")
        if fs.get("resolution"):
            text_parts.append(f"回收方式：{fs.get('resolution', '')}")
        
        if text_parts:
            items.append({
                "id": f"foreshadowing_{i}",
                "text": "，".join(text_parts),
                "metadata": {
                    "type": "foreshadowing",
                    "id": fs.get("id", f"fs_{i}")
                }
            })
    
    if items:
        success = await store.upsert_batch(collection="foreshadowing", items=items)
        print(f"  [OK] 伏笔回填完成: {len(items)} 条")
        return len(items) if success else 0
    
    return 0


async def backfill_project(project_id: str, types: list[str]) -> dict:
    """回填单个项目"""
    print(f"\n{'='*50}")
    print(f"项目: {project_id}")
    print(f"{'='*50}")
    
    store = VectorStore(project_id=project_id)
    
    results = {"bible": 0, "characters": 0, "foreshadowing": 0}
    
    if "bible" in types:
        results["bible"] = await backfill_bible(project_id, store)
    
    if "characters" in types:
        results["characters"] = await backfill_characters(project_id, store)
    
    if "foreshadowing" in types:
        results["foreshadowing"] = await backfill_foreshadowing(project_id, store)
    
    return results


async def main():
    parser = argparse.ArgumentParser(description="向量化回填脚本")
    parser.add_argument("--project-id", help="指定项目 ID（不指定则回填所有项目）")
    parser.add_argument("--type", default="bible,characters,foreshadowing",
                        help="回填类型，逗号分隔（bible/characters/foreshadowing）")
    args = parser.parse_args()
    
    types = [t.strip() for t in args.type.split(",")]
    
    workspace = Path(__file__).parent.parent / "workspace"
    
    if args.project_id:
        # 回填指定项目
        project_ids = [args.project_id]
    else:
        # 回填所有项目
        if not workspace.exists():
            print("[错误] 工作目录不存在")
            return
        
        project_ids = [d.name for d in workspace.iterdir() if d.is_dir()]
        print(f"发现 {len(project_ids)} 个项目")
    
    total_results = {"bible": 0, "characters": 0, "foreshadowing": 0}
    
    for project_id in project_ids:
        results = await backfill_project(project_id, types)
        for k, v in results.items():
            total_results[k] += v
    
    # 汇总
    print(f"\n{'='*50}")
    print(f"回填完成！")
    print(f"{'='*50}")
    print(f"  Bible: {total_results['bible']} 条")
    print(f"  人物: {total_results['characters']} 条")
    print(f"  伏笔: {total_results['foreshadowing']} 条")
    print(f"  总计: {sum(total_results.values())} 条")


if __name__ == "__main__":
    asyncio.run(main())
