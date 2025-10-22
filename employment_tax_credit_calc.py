# -*- coding: utf-8 -*-
"""
통합고용세액공제(조세특례제한법 제29조의8) 계산 스크립트 (템플릿)

⚠️ 중요: 본 스크립트는 "법 조문 및 시행령상의 단가/요건"을 외부 파라미터로 입력받아 계산하는
일반화 템플릿입니다. 실제 단가/요건(1인당 공제액, 유지기간, 제외업종 등)은 해당 연도 법령에 맞게
입력하세요.

기능 개요
- 세액공제액 계산 (상시근로자 증가, 청년등 증가, 정규직 전환, 육아휴직 복귀)
- 사후관리(유지기간 내 인원감소) 시 추징세액 계산 (방식 선택형: 비례/전액/티어드)
- 간단한 CLI (예시): JSON 파라미터 + 인원 입력값을 받아 결과 출력

작성자: ChatGPT
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Literal
import json
import math
import argparse


# -----------------------------
# 1) 기본 타입/데이터 클래스
# -----------------------------

class CompanySize(str, Enum):
    SME = "중소기업"
    MIDSIZE = "중견기업"
    LARGE = "대기업"


class Region(str, Enum):
    SEOUL_METRO = "수도권"
    NON_METRO = "지방"


@dataclass
class HeadcountInputs:
    """
    인원 입력값 (과세연도 기준)
    - prev_total: 직전 과세연도 상시근로자 수
    - curr_total: 당해 과세연도 상시근로자 수
    - prev_youth: 직전 연도 '청년등 상시근로자' 수
    - curr_youth: 당해 연도 '청년등 상시근로자' 수
    - converted_regular: 비정규직→정규직 전환 인원 (해당 과세연도)
    - returned_from_parental_leave: 육아휴직 복귀 인원 (해당 과세연도)
    """
    prev_total: int
    curr_total: int
    prev_youth: int = 0
    curr_youth: int = 0
    converted_regular: int = 0
    returned_from_parental_leave: int = 0

    @property
    def increase_total(self) -> int:
        return max(0, self.curr_total - self.prev_total)

    @property
    def increase_youth(self) -> int:
        return max(0, self.curr_youth - self.prev_youth)


@dataclass
class PolicyParameters:
    """
    법령/시행령에 따른 단가·기간·한도 설정
    - per_head_basic[size][region]: 상시근로자 증가 1인당 공제액
    - per_head_youth[size][region]: 청년등 증가 1인당 공제액
    - per_head_conversion: 정규직 전환 1인당 공제액 (규모/지역 동일 단가라면 단일값 사용)
    - per_head_return_from_parental: 육아휴직 복귀 1인당 공제액
    - retention_years[size]: 공제 후 유지기간(년)
    - max_credit_total (선택): 총 공제 한도 (없으면 None)
    - min_tax_limit_rate (선택): 최저한세 한도율 (예: 0.07). 세전 세액과 함께 제공 시 적용.
    - excluded_industries (선택): 제외 업종 코드 리스트
    """
    per_head_basic: Dict[CompanySize, Dict[Region, int]]
    per_head_youth: Dict[CompanySize, Dict[Region, int]]
    per_head_conversion: int = 0
    per_head_return_from_parental: int = 0
    retention_years: Dict[CompanySize, int] = None
    max_credit_total: Optional[int] = None
    min_tax_limit_rate: Optional[float] = None
    excluded_industries: Optional[list] = None


# -----------------------------
# 2) 계산 로직
# -----------------------------

def calc_gross_credit(
    size: CompanySize,
    region: Region,
    heads: HeadcountInputs,
    params: PolicyParameters,
) -> int:
    """
    통합고용세액공제 총공제액(최저한세·한도 적용 전) 계산
    공식:
      = increase_total * per_head_basic[size][region]
      + increase_youth * per_head_youth[size][region]
      + converted_regular * per_head_conversion
      + returned_from_parental_leave * per_head_return_from_parental
    """
    basic_unit = params.per_head_basic[size][region]
    youth_unit = params.per_head_youth[size][region]

    amount = (
        heads.increase_total * basic_unit
        + heads.increase_youth * youth_unit
        + heads.converted_regular * params.per_head_conversion
        + heads.returned_from_parental_leave * params.per_head_return_from_parental
    )
    return max(0, int(amount))


def apply_caps_and_min_tax(
    gross_credit: int,
    params: PolicyParameters,
    tax_before_credit: Optional[int] = None,
) -> int:
    """
    - 총공제한도(max_credit_total) 적용
    - 최저한세(min_tax_limit_rate) 적용: tax_before_credit가 주어진 경우에만 적용
      예) 세전 세액이 1억원, 한도율 7%라면 공제가능 최대는 7백만원
    """
    credit = gross_credit

    if params.max_credit_total is not None:
        credit = min(credit, int(params.max_credit_total))

    if params.min_tax_limit_rate is not None and tax_before_credit is not None:
        limit_by_min_tax = math.floor(params.min_tax_limit_rate * tax_before_credit)
        credit = min(credit, limit_by_min_tax)

    return max(0, int(credit))


def calc_clawback(
    credit_applied: int,
    base_headcount_at_credit: int,
    headcount_in_followup_year: int,
    retention_years_for_company: int,
    year_index_from_credit: int,
    method: Literal["proportional", "all_or_nothing", "tiered"] = "proportional",
    tiered_thresholds: Optional[Dict[str, float]] = None,
) -> int:
    """
    사후관리(유지기간 내 인원감소) 추징액 계산

    매개변수
    - credit_applied: 해당 과세연도 실제 적용된 공제액
    - base_headcount_at_credit: 공제연도 말 상시근로자 수 (heads.curr_total)
    - headcount_in_followup_year: 사후관리 대상 연도 말 상시근로자 수
    - retention_years_for_company: 유지기간(년) (회사규모별 상이)
    - year_index_from_credit: 공제연도로부터 몇 번째 연도(1, 2, 3 ...)
    - method:
        * proportional(비례추징): 감소비율만큼 추징
        * all_or_nothing(전액추징): 감소 발생 시 해당 연도분 전액 추징
        * tiered(구간추징): 임계비율에 따라 0/50/100% 등 단계적 추징
    - tiered_thresholds (tiered 전용):
        예시 {"none": 0.0, "half": 0.02, "full": 0.05}
        -> 감소율 < 2%: 0%, 2%~5%: 50%, ≥5%: 100%

    반환: 해당 사후관리 연도별 추징세액 (원단위 정수)
    """
    if year_index_from_credit < 1 or year_index_from_credit > retention_years_for_company:
        return 0

    decrease = max(0, base_headcount_at_credit - headcount_in_followup_year)
    if base_headcount_at_credit <= 0 or decrease <= 0:
        return 0

    decrease_ratio = decrease / float(base_headcount_at_credit)

    if method == "proportional":
        return int(round(credit_applied * decrease_ratio))

    if method == "all_or_nothing":
        return int(credit_applied) if decrease > 0 else 0

    if method == "tiered":
        # 기본 임계값
        thresholds = tiered_thresholds or {"none": 0.0, "half": 0.02, "full": 0.05}
        if decrease_ratio < thresholds.get("half", 0.02):
            return 0
        elif decrease_ratio < thresholds.get("full", 0.05):
            return int(round(credit_applied * 0.5))
        else:
            return int(credit_applied)

    # 기본값(안전장치): 비례
    return int(round(credit_applied * decrease_ratio))


# -----------------------------
# 3) 유틸 & CLI
# -----------------------------

def load_params_from_json(path: str) -> PolicyParameters:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # JSON -> Enum key 변환
    def _to_size(k: str) -> CompanySize:
        mapping = {
            "중소기업": CompanySize.SME,
            "중견기업": CompanySize.MIDSIZE,
            "대기업": CompanySize.LARGE,
        }
        return mapping[k]

    def _to_region(k: str) -> Region:
        mapping = {"수도권": Region.SEOUL_METRO, "지방": Region.NON_METRO}
        return mapping[k]

    per_head_basic = {
        _to_size(sk): { _to_region(rk): int(v) for rk, v in sv.items() }
        for sk, sv in cfg["per_head_basic"].items()
    }
    per_head_youth = {
        _to_size(sk): { _to_region(rk): int(v) for rk, v in sv.items() }
        for sk, sv in cfg["per_head_youth"].items()
    }
    retention_years = { _to_size(k): int(v) for k, v in cfg["retention_years"].items() }

    return PolicyParameters(
        per_head_basic=per_head_basic,
        per_head_youth=per_head_youth,
        per_head_conversion=int(cfg.get("per_head_conversion", 0)),
        per_head_return_from_parental=int(cfg.get("per_head_return_from_parental", 0)),
        retention_years=retention_years,
        max_credit_total=(int(cfg["max_credit_total"]) if cfg.get("max_credit_total") is not None else None),
        min_tax_limit_rate=(float(cfg["min_tax_limit_rate"]) if cfg.get("min_tax_limit_rate") is not None else None),
        excluded_industries=cfg.get("excluded_industries"),
    )


def main():
    parser = argparse.ArgumentParser(description="통합고용세액공제 계산기 (템플릿)")
    parser.add_argument("--company-size", choices=[s.value for s in CompanySize], required=True)
    parser.add_argument("--region", choices=[r.value for r in Region], required=True)
    parser.add_argument("--params-json", required=True, help="법령 단가·기간 설정 JSON 경로")
    parser.add_argument("--prev-total", type=int, required=True)
    parser.add_argument("--curr-total", type=int, required=True)
    parser.add_argument("--prev-youth", type=int, default=0)
    parser.add_argument("--curr-youth", type=int, default=0)
    parser.add_argument("--converted-regular", type=int, default=0)
    parser.add_argument("--returned-parental", type=int, default=0)
    parser.add_argument("--tax-before-credit", type=int, default=None, help="최저한세 적용 시 세전세액")
    parser.add_argument("--clawback-followup", type=int, default=None, help="사후관리 연도 말 상시근로자수(예: 공제+1년차)")
    parser.add_argument("--clawback-year-index", type=int, default=1, help="공제연도로부터 n년차(1~유지기간)")
    parser.add_argument("--clawback-method", choices=["proportional", "all_or_nothing", "tiered"], default="proportional")

    args = parser.parse_args()

    size = CompanySize(args.company_size)
    region = Region(args.region)
    params = load_params_from_json(args.params_json)

    heads = HeadcountInputs(
        prev_total=args.prev_total,
        curr_total=args.curr_total,
        prev_youth=args.prev_youth,
        curr_youth=args.curr_youth,
        converted_regular=args.converted_regular,
        returned_from_parental_leave=args.returned_parental,
    )

    gross = calc_gross_credit(size, region, heads, params)
    applied = apply_caps_and_min_tax(gross, params, tax_before_credit=args.tax_before_credit)
    retention = params.retention_years[size]

    print("=== 통합고용세액공제 계산 결과 ===")
    print(f"- 기업규모 / 지역: {size.value} / {region.value}")
    print(f"- 직전/당해 상시근로자수: {heads.prev_total} -> {heads.curr_total} (증가 {heads.increase_total}명)")
    print(f"- 직전/당해 청년등: {heads.prev_youth} -> {heads.curr_youth} (증가 {heads.increase_youth}명)")
    print(f"- 정규직 전환: {heads.converted_regular}명, 육아휴직 복귀: {heads.returned_from_parental_leave}명")
    print(f"- 총공제액(최저한세/한도 적용 전): {gross:,}원")
    print(f"- 적용 공제액(최저한세/한도 적용 후): {applied:,}원")
    print(f"- 유지기간(회사규모별): {retention}년")

    # 사후관리(옵션)
    if args.clawback_followup is not None:
        clawback = calc_clawback(
            credit_applied=applied,
            base_headcount_at_credit=heads.curr_total,
            headcount_in_followup_year=args.clawback_followup,
            retention_years_for_company=retention,
            year_index_from_credit=args.clawback_year_index,
            method=args.clawback_method,
        )
        print("\n--- 사후관리(추징) 시뮬레이션 ---")
        print(f"- 공제연도 말 상시근로자수: {heads.curr_total}명")
        print(f"- 사후연도({args.clawback_year_index}년차) 말 상시근로자수: {args.clawback_followup}명")
        print(f"- 추징방식: {args.clawback_method}")
        print(f"- 추징세액: {clawback:,}원")


if __name__ == "__main__":
    # JSON 파라미터 예시 (참고용):
    # {
    #   "per_head_basic": {
    #     "중소기업": {"수도권": 1000000, "지방": 1100000},
    #     "중견기업": {"수도권": 800000, "지방": 900000},
    #     "대기업":   {"수도권": 500000, "지방": 600000}
    #   },
    #   "per_head_youth": {
    #     "중소기업": {"수도권": 1300000, "지방": 1400000},
    #     "중견기업": {"수도권": 1000000, "지방": 1100000},
    #     "대기업":   {"수도권": 700000,  "지방": 800000}
    #   },
    #   "per_head_conversion": 700000,
    #   "per_head_return_from_parental": 700000,
    #   "retention_years": {"중소기업": 3, "중견기업": 3, "대기업": 2},
    #   "max_credit_total": null,
    #   "min_tax_limit_rate": 0.07,
    #   "excluded_industries": ["유흥주점업", "기타소비성서비스업"]
    # }
    main()
