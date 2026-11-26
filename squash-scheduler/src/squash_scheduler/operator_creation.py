"""Reviewed group creation and draw editing on the canonical tournament snapshot."""

from copy import deepcopy

from .domain.draws import draw_inputs, run_draw
from .domain.models import Division, Entry, Participant, Registration, canonical_hash, stable_id
from .domain.rules import refresh_rule_snapshots


def apply_coach_rules(snapshot):
    if snapshot.status == "approved":
        raise ValueError("請先建立可編輯草稿，再確認競賽規則。")
    if any(
        d.get("kind") == "encouragement" and d.get("status") != "superseded" for d in snapshot.draws
    ):
        raise ValueError("已有鼓勵賽籤表，請保留原規則並由賽務另行處理。")
    snapshot.config.setdefault("runtime", {})["coach_competition_rules"] = True
    refresh_rule_snapshots(snapshot)
    return {
        "title": "確認教練競賽規則",
        "notice": "冠軍賽五戰三勝，其餘三戰兩勝；同分採同分者彼此對戰商數；直接淘汰首輪實際敗者含種子可打鼓勵賽，僅取冠軍。已開打場次保留固定規則。",
    }


def add_division(snapshot, roster):
    if snapshot.status == "approved" or any(
        m.status in {"in_progress", "completed"} for m in snapshot.matches
    ):
        raise ValueError("已核准或開打的賽事不能新增組別，請使用賽前草稿。")
    if not isinstance(roster, dict) or set(roster) != {
        "name",
        "names",
        "category",
        "gender_category",
        "reuse_existing",
    }:
        raise ValueError("請提供組名、選手名單、組別分類與性別分類。")
    name, names = roster["name"], roster["names"]
    group_name = name
    if (
        not isinstance(name, str)
        or not name.strip()
        or any(d.name == name.strip() for d in snapshot.divisions)
    ):
        raise ValueError("組名不可空白或與既有組別相同。")
    if (
        not isinstance(names, list)
        or not 2 <= len(names) <= 256
        or any(not isinstance(n, str) or not n.strip() for n in names)
    ):
        raise ValueError("每組請輸入 2 至 256 位選手，每行一人。")
    names = [n.strip() for n in names]
    if type(roster["reuse_existing"]) is not bool:
        raise ValueError("請明確確認是否沿用既有同名選手。")
    people_by_name = {}
    for person in snapshot.participants:
        people_by_name.setdefault(person.official_name, []).append(person)
    for name in names:
        matches = people_by_name.get(name, [])
        if matches and not roster["reuse_existing"]:
            raise ValueError(f"{name} 已在其他組名單，請明確勾選沿用同名選手，或補上可區分的姓名。")
        if len(matches) > 1:
            raise ValueError(f"{name} 對應多位既有選手，請先完成身分確認。")
    if len(set(names)) != len(names):
        raise ValueError("同組姓名重複，請補上可區分的姓名。")
    if roster["category"] not in {"competitive", "school"} or roster["gender_category"] not in {
        "male",
        "female",
        "mixed",
    }:
        raise ValueError("請選擇組別及性別分類。")
    division = Division(
        id=stable_id(snapshot.id, "manual-division", canonical_hash(roster)),
        name=group_name.strip(),
        status="active",
        minimum_entries=2,
        category=roster["category"],
        gender_category=roster["gender_category"],
    )
    snapshot.divisions.append(division)
    source_id = stable_id(division.id, "reviewed-roster-source")
    snapshot.sources.append(
        {
            "id": source_id,
            "media_type": "application/json",
            "original_name": "reviewed-group-roster.json",
            "sha256": canonical_hash(roster),
            "reviewed_roster": deepcopy(roster),
        }
    )
    for index, name in enumerate(names):
        person = (
            people_by_name[name][0]
            if name in people_by_name
            else Participant(
                id=stable_id(division.id, "person", str(index)),
                official_name=name,
                display_name=name,
            )
        )
        registration = Registration(
            id=stable_id(division.id, "registration", str(index)),
            participant_id=person.id,
            original_division_id=division.id,
            source_ref={
                "source_file_id": source_id,
                "manual_entry": True,
                "reviewed_group_creation": True,
            },
        )
        if person.id not in {p.id for p in snapshot.participants}:
            snapshot.participants.append(person)
        snapshot.registrations.append(registration)
        snapshot.entries.append(
            Entry(
                id=stable_id(division.id, "entry", str(index)),
                registration_id=registration.id,
                effective_division_id=division.id,
            )
        )
    snapshot.config["divisions"] = [d.model_dump(mode="json") for d in snapshot.divisions]
    snapshot.config.setdefault("registration", {}).setdefault("division_aliases", {})[
        division.name
    ] = division.id
    return {
        "title": "新增組別名單",
        "division_id": division.id,
        "roster_names": names,
        "notice": f"{division.name} 共 {len(names)} 人。下一步選擇本組賽制，再產生及調整籤表。",
    }


def edit_draw(snapshot, draw_id, positions, actor, reason):
    draw = next((d for d in reversed(snapshot.draws) if d.get("id") == draw_id), None)
    if (
        not draw
        or draw.get("kind") in {"encouragement", "deferred_slots"}
        or draw.get("status") not in {"draft", "review_required"}
    ):
        raise ValueError("只能調整尚未確認的最新主賽籤表。")
    division = next(d for d in snapshot.divisions if d.id == draw["division_id"])
    latest = next(
        d
        for d in reversed(snapshot.draws)
        if d.get("division_id") == division.id
        and d.get("kind") not in {"encouragement", "deferred_slots"}
        and d.get("status") != "superseded"
    )
    ids = {m.id for m in snapshot.matches if m.division_id == division.id}
    if (
        latest is not draw
        or any(m.id in ids and m.status != "planned" for m in snapshot.matches)
        or any(r.get("match_id") in ids for r in snapshot.results)
        or any(a.match_id in ids and any(a.locks.values()) for a in snapshot.assignments)
    ):
        raise ValueError("此組已鎖定或籤表已更新，不能調整。")
    original_slots = draw["output"]["slots"]
    if (
        not isinstance(positions, list)
        or len(positions) != len(original_slots)
        or sorted(str(x) for x in positions) != sorted(str(s["entry_id"]) for s in original_slots)
    ):
        raise ValueError("每位選手及輪空須恰好保留一次。")
    fixed = {entry: str(i + 1) for i, entry in enumerate(positions) if entry is not None}
    original = deepcopy(division.draw_overrides)
    existing = {
        **snapshot.config.get("draw_defaults", {}).get("fixed_positions", {}),
        **original.get("fixed_positions", {}),
    }
    if any(fixed.get(entry) != str(position) for entry, position in existing.items()):
        raise ValueError("調整不得違反既有固定籤位。")
    try:
        division.draw_overrides["fixed_positions"] = fixed
        record = run_draw(snapshot, division.id, draw["random_seed"], actor, reason)
    finally:
        division.draw_overrides = original
    if record["output"] is None:
        raise ValueError("調整違反種子、輪空或同單位迴避限制。")
    if [s["entry_id"] for s in record["output"]["slots"]] != positions:
        raise ValueError("調整違反固定輪空位置。")
    record["inputs"] = draw_inputs(snapshot, division.id, draw["random_seed"])
    record["input_hash"] = canonical_hash(record["inputs"])
    record["constraints"] = record["inputs"]["constraints"]
    record["algorithm_version"] = "reviewed-manual-positions-v1"
    record["supersedes_draw_id"] = draw_id
    draw["status"] = "superseded"
    return {
        "title": "調整主賽籤位提案",
        "division_id": division.id,
        "output": record["output"],
        "notice": "確認本次調整後，仍須確認籤表才會更新對戰。",
    }
