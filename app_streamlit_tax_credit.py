# -*- coding: utf-8 -*-
import streamlit as st
import json
import io
import os

# 로컬 모듈 임포트 (동일 폴더에 employment_tax_credit_calc.py가 있어야 합니다)
from employment_tax_credit_calc import (
    CompanySize, Region, HeadcountInputs,
    load_params_from_json, calc_gross_credit,
    apply_caps_and_min_tax, calc_clawback, PolicyParameters
)

st.set_page_config(page_title="통합고용세액공제 계산기", layout="wide")

st.title("통합고용세액공제 계산기 (조특법 §29조의8)")
st.caption("파라미터(JSON)만 바꾸면 연도별 법령 단가/요건을 반영할 수 있습니다.")

with st.sidebar:
    st.header("1) 정책 파라미터 불러오기")
    uploaded = st.file_uploader("시행령 기준 파라미터 JSON 업로드", type=["json"], accept_multiple_files=False)
    default_info = st.toggle("예시 파라미터 사용 (업로드 없을 때)", value=True)

    params: PolicyParameters = None
    if uploaded is not None:
        try:
            cfg = json.load(uploaded)
            # 임시 파일 저장 후 로드 (모듈의 JSON 로더 재사용)
            tmp_path = "._tmp_params.json"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False)
            params = load_params_from_json(tmp_path)
            os.remove(tmp_path)
            st.success("업로드한 파라미터를 불러왔습니다.")
        except Exception as e:
            st.error(f"파라미터 로딩 실패: {e}")
    elif default_info:
        # 데모용 기본 파라미터
        demo_cfg = {
            "per_head_basic": {
                "중소기업": {"수도권": 1200000, "지방": 1300000},
                "중견기업": {"수도권": 900000, "지방": 1000000},
                "대기업":   {"수도권": 600000, "지방": 700000}
            },
            "per_head_youth": {
                "중소기업": {"수도권": 1500000, "지방": 1600000},
                "중견기업": {"수도권": 1100000, "지방": 1200000},
                "대기업":   {"수도권": 800000,  "지방": 900000}
            },
            "per_head_conversion": 800000,
            "per_head_return_from_parental": 800000,
            "retention_years": {"중소기업": 3, "중견기업": 3, "대기업": 2},
            "max_credit_total": None,
            "min_tax_limit_rate": 0.07,
            "excluded_industries": ["유흥주점업", "기타소비성서비스업"]
        }
        tmp_path = "._tmp_params_demo.json"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(demo_cfg, f, ensure_ascii=False)
        params = load_params_from_json(tmp_path)
        os.remove(tmp_path)
        st.info("예시 파라미터를 사용 중입니다. (업로드 시 자동 대체)")

    st.divider()
    st.header("2) 기업 정보")
    size_label = st.selectbox("기업규모", [s.value for s in CompanySize], index=0)
    region_label = st.selectbox("지역", [r.value for r in Region], index=1)
    size = CompanySize(size_label)
    region = Region(region_label)

    st.divider()
    st.header("3) 사후관리 옵션")
    clawback_method = st.selectbox("추징 방식", ["proportional", "all_or_nothing", "tiered"], index=0)
    clawback_year_index = st.number_input("사후관리 연차 (1부터 유지기간 이내)", min_value=1, value=1, step=1)

st.header("고용 인원 입력")
col1, col2, col3 = st.columns(3)

with col1:
    prev_total = st.number_input("전년 상시근로자 수", min_value=0, value=50, step=1)
    prev_youth = st.number_input("전년 청년등 상시근로자 수", min_value=0, value=10, step=1)
with col2:
    curr_total = st.number_input("당해 상시근로자 수", min_value=0, value=60, step=1)
    curr_youth = st.number_input("당해 청년등 상시근로자 수", min_value=0, value=14, step=1)
with col3:
    converted_regular = st.number_input("정규직 전환 인원", min_value=0, value=2, step=1)
    returned_parental = st.number_input("육아휴직 복귀 인원", min_value=0, value=1, step=1)

st.header("세액 한도/최저한세 옵션")
tax_before_credit = st.number_input("세전세액(최저한세 적용 시 필요)", min_value=0, value=120_000_000, step=1, help="입력하지 않으면 최저한세 한도는 적용하지 않습니다.")

st.divider()
run = st.button("계산하기", type="primary", disabled=(params is None))

if run:
    if params is None:
        st.error("파라미터(JSON)를 먼저 불러오세요.")
    else:
        heads = HeadcountInputs(
            prev_total=int(prev_total),
            curr_total=int(curr_total),
            prev_youth=int(prev_youth),
            curr_youth=int(curr_youth),
            converted_regular=int(converted_regular),
            returned_from_parental_leave=int(returned_parental),
        )
        gross = calc_gross_credit(size, region, heads, params)
        applied = apply_caps_and_min_tax(gross, params, tax_before_credit=int(tax_before_credit) if tax_before_credit else None)
        retention_years = params.retention_years[size]

        st.subheader("① 공제액 계산 결과")
        st.metric("총공제액 (전)", f"{gross:,} 원")
        st.metric("적용 공제액 (후)", f"{applied:,} 원")
        st.write(f"유지기간(사후관리 대상): **{retention_years}년**")

        st.subheader("② 사후관리(추징) 시뮬레이션")
        followup = st.number_input("사후관리 연도 말 상시근로자 수", min_value=0, value=max(0, int(curr_total) - 3), step=1)
        clawback = calc_clawback(
            credit_applied=applied,
            base_headcount_at_credit=int(curr_total),
            headcount_in_followup_year=int(followup),
            retention_years_for_company=retention_years,
            year_index_from_credit=int(clawback_year_index),
            method=clawback_method,
        )
        st.metric("추징세액", f"{clawback:,} 원")
        st.caption("※ 감소율·방식(비례/전액/티어드)에 따라 상이합니다.")

        st.subheader("③ 세부 입력/출력 JSON 내려받기")
        payload = {
            "inputs": {
                "company_size": size.value,
                "region": region.value,
                "prev_total": int(prev_total),
                "curr_total": int(curr_total),
                "prev_youth": int(prev_youth),
                "curr_youth": int(curr_youth),
                "converted_regular": int(converted_regular),
                "returned_parental": int(returned_parental),
                "tax_before_credit": int(tax_before_credit) if tax_before_credit else None,
                "clawback_followup": int(followup),
                "clawback_year_index": int(clawback_year_index),
                "clawback_method": clawback_method,
            },
            "results": {
                "gross_credit": int(gross),
                "applied_credit": int(applied),
                "retention_years": int(retention_years),
                "clawback_amount": int(clawback),
            }
        }
        st.download_button(
            label="JSON 다운로드",
            file_name="tax_credit_result.json",
            mime="application/json",
            data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        )

        with st.expander("참고: 사용 중인 정책 파라미터 보기"):
            st.code(json.dumps({
                "per_head_basic": {k.value: {kk.value: v for kk, v in d.items()} for k, d in params.per_head_basic.items()},
                "per_head_youth": {k.value: {kk.value: v for kk, v in d.items()} for k, d in params.per_head_youth.items()},
                "per_head_conversion": params.per_head_conversion,
                "per_head_return_from_parental": params.per_head_return_from_parental,
                "retention_years": {k.value: v for k, v in params.retention_years.items()},
                "max_credit_total": params.max_credit_total,
                "min_tax_limit_rate": params.min_tax_limit_rate,
                "excluded_industries": params.excluded_industries,
            }, ensure_ascii=False, indent=2), language="json")

else:
    st.info("좌측에서 파라미터(JSON)를 불러오고, 인원을 입력한 뒤 **계산하기**를 눌러주세요.")
