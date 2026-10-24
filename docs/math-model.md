# 第一阶段数学模型 / Phase-one mathematical model

时间步 `t` 等长 `Δt` 小时。功率 kW，能量 kWh，电价 CNY/kWh；正值表示购电、充电、放电、用电。 / Uniform time steps; positive values denote import, charge, discharge and load.

输入 / Inputs: 负荷 `L[t]`、可用光伏 `A[t]`、电价 `p[t]`、购电上界 `Gmax`、电池容量 `Emax`、充放电功率上界 `Cmax/Dmax`、效率 `ηc/ηd`、初末能量 `E0/Eend`。

变量 / Variables: 购电 `G[t]`、充电 `C[t]`、放电 `D[t]`、光伏利用 `P[t]`、期末能量 `E[t]`、充电模式二元变量 `z[t]`。弃光 `A[t]-P[t]`。 / Grid, charge, discharge, PV used, end-of-step energy, binary charge mode; curtailment is available minus used PV.

目标 / Objective: `min Σ G[t] × p[t] × Δt`，仅计购电电费，不含需量费、退化、售电或排放。 / Minimize import cost only.

约束 / Constraints:

```text
G[t] + D[t] + P[t] = L[t] + C[t]
E[t] = (E0 if t=0 else E[t-1]) + Δt(ηc C[t] - D[t]/ηd)
0 ≤ E[t] ≤ Emax; 0 ≤ G[t] ≤ Gmax; 0 ≤ P[t] ≤ A[t]
0 ≤ C[t] ≤ Cmax z[t]; 0 ≤ D[t] ≤ Dmax(1-z[t]); z[t] ∈ {0,1}
E[last] = Eend
```

无储能基线同一输入，但 `C=D=0` 且终端能量保持 `E0`。两者都是可行性与成本优化问题；当基线因购电上限不可行时不生成比较结果。 / The no-storage baseline fixes charge and discharge to zero; comparison is withheld if it is infeasible.

模型未表示储能自放电、电网出口、线路损耗、光伏预测误差或真实控制器。优化效果仅针对本输入与假设，不能推断校园节能量。 / Self-discharge, exports, losses, uncertainty and real controls are outside this model. Cost difference is not a campus energy-saving claim.
