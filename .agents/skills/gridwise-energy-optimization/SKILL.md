---
name: gridwise-energy-optimization
description: Review or change GridWise Campus offline dispatch models, solver handling, baseline comparisons, or optimization result validation. Use only for energy optimization work in this repository.
---

When changing the offline model, read `docs/math-model.md`, `docs/data-dictionary.md`, and the current implementation. Keep power in kW, energy in kWh, price in CNY/kWh, and time step in hours. Check physical balance, SOC flow, bounds, initial and terminal policy, and whether charging and discharging can coincide.

Compare optimized and no-storage runs only on the same validated input. Require an optimal solver termination and independently check exported numbers with arithmetic outside Pyomo. Add or update behavior tests for changed constraints, including an infeasible case when relevant. Report model assumptions, verified outcomes, and untested limits. Never treat the offline model as authorization to read campus systems or control equipment.
