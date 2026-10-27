# 第一阶段数学模型 / Phase-one mathematical model

时间步 `t` 等长 `Δt` 小时。功率 kW，能量 kWh，电价 CNY/kWh；正值表示购电、充电、放电、用电。 / Uniform time steps; positive values denote import, charge, discharge and load.

输入 / Inputs: 负荷 `L[t]`、可用光伏 `A[t]`、电价 `p[t]`、购电上界 `Gmax`、电池容量 `Emax`、充放电功率上界 `Cmax/Dmax`、效率 `ηc/ηd`、初末能量 `E0/Eend`。

v0.3.14 桌面允许用户显式配置 `Gmax`、`Emax`、`Cmax`、`Dmax`、`E0`、`Eend`、`ηc` 和 `ηd`，但没有改变下列方程。界面中的“初始/终端 SOC”按现有模型表示为 **kWh 能量**，不是百分比；`0 ≤ E0,Eend ≤ Emax`，`Emax>0`，`Cmax,Dmax,Gmax≥0`，`0<ηc,ηd≤1`。零功率等合法参数仍可能使模型不可行，此时必须失败关闭。/ The desktop now exposes the existing equipment constants without changing the equations. Initial and terminal SOC are energy in kWh, not percentages; valid values may still form an infeasible scenario, which fails closed.

变量 / Variables: 购电 `G[t]`、充电 `C[t]`、放电 `D[t]`、光伏利用 `P[t]`、期末能量 `E[t]`、充电模式二元变量 `z[t]`。弃光 `A[t]-P[t]`。 / Grid, charge, discharge, PV used, end-of-step energy, binary charge mode; curtailment is available minus used PV.

目标 / Objective: `min Σ G[t] × p[t] × Δt`，仅计购电电费，不含需量费、退化、售电或排放。 / Minimize import cost only.

v0.3.11 可选削峰上限模式 / Optional peak-cap mode: 显式传入 `K`（kW，`0 ≤ K ≤ Gmax`）时，仅对储能优化方案增加逐时约束 `G[t] ≤ K`；目标仍是上述纯电费最小化，无储能基线保持同一输入和原约束，不施加 `K`。未传入时保持原模型语义。`K` 导致不可行时报告求解失败，不自动放宽上限。/ With an explicit cap `K`, constrain each optimized period's import while retaining the cost objective. The no-storage baseline is unchanged. Infeasibility is reported, never relaxed.

约束 / Constraints:

```text
G[t] + D[t] + P[t] = L[t] + C[t]
E[t] = (E0 if t=0 else E[t-1]) + Δt(ηc C[t] - D[t]/ηd)
0 ≤ E[t] ≤ Emax; 0 ≤ G[t] ≤ Gmax; 0 ≤ P[t] ≤ A[t]
0 ≤ C[t] ≤ Cmax z[t]; 0 ≤ D[t] ≤ Dmax(1-z[t]); z[t] ∈ {0,1}
E[last] = Eend
```

无储能基线同一输入，但 `C=D=0` 且终端能量保持 `E0`。两者都是可行性与成本优化问题；当基线因购电上限不可行时不生成比较结果。 / The no-storage baseline fixes charge and discharge to zero; comparison is withheld if it is infeasible.

三项比较指标独立定义 / Independent metrics: `total_cost_cny = Σ G[t]p[t]Δt`，`grid_energy_kwh = Σ G[t]Δt`，`peak_grid_kw = max G[t]`。峰值上限是逐时功率约束，并非需量费或新的目标项；电费、购电量和峰值可能朝不同方向变化。/ The cap constrains period power; it does not add a demand charge or peak term to the objective.

模型未表示储能自放电、电网出口、线路损耗、光伏预测误差或真实控制器。优化效果仅针对本输入与假设，不能推断校园节能量。 / Self-discharge, exports, losses, uncertainty and real controls are outside this model. Cost difference is not a campus energy-saving claim.
