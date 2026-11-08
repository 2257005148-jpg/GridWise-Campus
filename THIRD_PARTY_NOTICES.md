# Third-Party Software and License Inventory

This document records the third-party Python packages and native components reviewed for GridWise Campus. It is an inventory for a **source-only release**: the repository does not bundle Python, Tcl/Tk, third-party Wheels, native DLLs, an offline dependency archive, or the Microsoft Visual C++ Runtime. Users obtain dependencies separately from their upstream distributors.

Project-owned GridWise Campus source code is prepared for release under Apache License 2.0. That license does not relicense third-party software. The license terms of each dependency continue to apply to that dependency.

本文件记录 GridWise Campus 已审计的第三方 Python 包与原生组件。首版采用**纯源码发行**：仓库不捆绑 Python、Tcl/Tk、第三方 Wheel、原生 DLL、离线依赖包或 Microsoft Visual C++ Runtime，依赖由用户自行从上游发行渠道安装。

GridWise Campus 项目自有源代码已按 Apache License 2.0 准备发行；该许可证不会改变第三方软件本身的许可证。每个依赖仍受其上游许可证约束。

## Distribution roles and boundaries

| Role | Included in the source-only repository | Installation / redistribution boundary |
|---|---|---|
| Project-owned Python source, tests, documentation, and synthetic examples | Yes | Prepared for release under Apache License 2.0; author ownership is an owner assertion, not an independent legal certification |
| Direct runtime dependencies (`pyomo`, `highspy`) | No | Declared in project metadata and installed separately by the user |
| Transitive runtime/test dependencies | No | Resolved and installed separately by the user's Python package installer |
| Build dependency (`setuptools`) | No | Downloaded into an isolated build environment; not an end-user runtime dependency and not bundled in this source release |
| Native extensions and runtime libraries found inside audited Wheels | No | Not redistributed by this source-only release; any future binary or offline bundle requires a new component-by-component review |

## Audit scope and status labels

The evidence below comes from the v0.3.32 inspection of the actual CPython 3.12 Windows x64 Wheels, their `METADATA`, embedded license files, and the official PLY 3.11 source distribution.

- `COMPATIBLE`: sufficient technical evidence for coexistence with Apache-2.0, subject to ordinary attribution and license-retention duties.
- `CONDITIONAL`: technically compatible only when the stated additional notice or license-text duty is met.
- `REVIEW_REQUIRED`: legal or technical evidence remains incomplete.
- `UNKNOWN`: evidence is insufficient to classify the component.

These are engineering audit classifications, not independent legal opinions.

## Python packages

| Package | Role | Upstream | License evidence and copyright | Redistribution obligation | Audit status |
|---|---|---|---|---|---|
| colorama 0.4.6 | Test/runtime transitive dependency | [tartley/colorama](https://github.com/tartley/colorama) | BSD-3-Clause; Copyright 2010 Jonathan Hartley | If redistributed, retain the BSD notice, conditions and disclaimer. No Wheel is bundled in the source-only release. | COMPATIBLE |
| highspy 1.15.1 | Direct runtime dependency | [ERGO-Code/HiGHS](https://github.com/ERGO-Code/HiGHS) | HiGHS MIT license; Copyright 2026 HiGHS under the audit evidence. Its notice also identifies AMD (BSD-3-Clause), CLI11 (BSD-3-Clause-like), METIS (Apache-2.0), pdqsort (Zlib), rcm (MIT), and zstr (MIT). | If the Wheel or binaries are redistributed, retain the HiGHS license and applicable component notices. The Wheel's Microsoft runtime redistribution basis is not established by the Wheel contents. | REVIEW_REQUIRED |
| iniconfig 2.3.1 | pytest transitive dependency | [pytest-dev/iniconfig](https://github.com/pytest-dev/iniconfig) | MIT; Copyright Holger Krekel and others, 2010-2023 | If redistributed, retain the MIT copyright and permission notice. | COMPATIBLE |
| numpy 2.5.4 | Runtime dependency of Pyomo/HiGHS paths | [numpy/numpy](https://github.com/numpy/numpy) | Main BSD-3-Clause license; Copyright 2005-2025 NumPy Developers. Wheel evidence also covers 0BSD, MIT, Zlib, CC0-1.0, OpenBLAS BSD-3-Clause, LAPACK BSD-3-Clause-Open-MPI, and GCC runtime terms. | If redistributed, retain all applicable license files and notices for NumPy and bundled components. The GCC runtime is `GPL-3.0-or-later WITH GCC-exception-3.1`; it must not be described as ordinary GPL without the Runtime Library Exception. Microsoft runtime rights remain unresolved. | REVIEW_REQUIRED |
| packaging 26.3 | pytest/setuptools transitive dependency | [pypa/packaging](https://github.com/pypa/packaging) | Apache-2.0 OR BSD-2-Clause; Copyright Donald Stufft and contributors | A redistributor may follow either offered license and must retain its required notices. | COMPATIBLE |
| pluggy 1.7.0 | pytest transitive dependency | [pytest-dev/pluggy](https://github.com/pytest-dev/pluggy) | MIT; Copyright Holger Krekel, 2015 | If redistributed, retain the MIT copyright and permission notice. | COMPATIBLE |
| PLY 3.11 | Pyomo transitive dependency | [dabeaz/ply](https://github.com/dabeaz/ply) | BSD-3-Clause; Copyright 2001-2018 David M. Beazley / Dabeaz LLC. The Wheel contained no license file; the complete license text was obtained from the official PyPI source distribution README. | Any redistribution must include the BSD-3-Clause notice, conditions and disclaimer obtained from the official sdist. | CONDITIONAL |
| Pygments 2.21.0 | pytest transitive dependency | [pygments/pygments](https://github.com/pygments/pygments) | BSD-2-Clause; Copyright 2006-2022 by the respective authors | If redistributed, retain the copyright notice, conditions and disclaimer. | COMPATIBLE |
| Pyomo 6.9.5 | Direct runtime dependency | [Pyomo/pyomo](https://github.com/Pyomo/pyomo) | BSD-3-Clause; Copyright 2008-2025 National Technology and Engineering Solutions of Sandia, LLC; upstream text includes United States Government rights language | If redistributed, retain the complete BSD license, copyright notice, conditions, disclaimer, and applicable government-rights statement. | COMPATIBLE |
| pytest 8.4.2 | Optional test dependency | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | MIT; Copyright Holger Krekel and others, 2004 onward | If redistributed, retain the MIT copyright and permission notice. | COMPATIBLE |
| setuptools 84.2.1 | PEP 517 build tool, not an end-user runtime dependency | [pypa/setuptools](https://github.com/pypa/setuptools) | Main MIT license. The audited Wheel also vendors LGPL-3.0, MPL-2.0, BSD, Apache-2.0 and MIT components and notices. | The source-only repository declares a build backend but does not bundle the setuptools Wheel. If a future release bundles it, all vendored component licenses and notices must be preserved; the top-level MIT metadata alone is insufficient. | REVIEW_REQUIRED |

## Audited Wheel digests

The SHA-256 values identify the exact PyPI artifacts inspected in v0.3.32. Matching a PyPI digest identifies the published artifact; it is not proof of publisher identity or reproducible build provenance.

| Wheel | SHA-256 |
|---|---|
| `colorama-0.4.6-py2.py3-none-any.whl` | `4F1D9991F5ACC0CA119F9D443620B77F9D6B33703E51011C16BAF57AFB285FC6` |
| `highspy-1.15.1-cp312-cp312-win_amd64.whl` | `8C548165270608A40147A7EA6D985FD62A65FABF0F075B3C0C59EA910B724223` |
| `iniconfig-2.3.1-py3-none-any.whl` | `9121E2C1FDB355232495BE3194C8DFE87CCC2D5DEE45947B78E68F499790D7A7` |
| `numpy-2.5.4-cp312-cp312-win_amd64.whl` | `A7B1B6353E36A7E50DE2973A38D705C88EE93ADCF120673CEE7F45A4A3FA223A` |
| `packaging-26.3-py3-none-any.whl` | `D7193F7C8E4E93F444FDE0262BF90AF30E16FA0AD0AD44CB553C87339B23CD1C` |
| `pluggy-1.7.0-py3-none-any.whl` | `7DD7B0D8832BA3CB632C306926DED123429211B83641B35DC5C41AD2D34F9BEC` |
| `ply-3.11-py2.py3-none-any.whl` | `096F9B8350B65EBD2FD1346B12452EFE5B9607F7482813FFCA50C22722A807CE` |
| `pygments-2.21.0-py3-none-any.whl` | `2363C69B61C4A97C838DA3B130DCD6468F4848992B21A82F2A63EC34377137D9` |
| `pyomo-6.9.5-py3-none-any.whl` | `60326F7D3143EE7D0F5C5C4A3CBF871B53E08CC6C2B0C9E6D25568880233472F` |
| `pytest-8.4.2-py3-none-any.whl` | `872F880DE3FC3A5BDC88A11B39C9710C3497A547CFA9320BC3C5E62FBF272E79` |
| `setuptools-84.2.1-py3-none-any.whl` | `CA140B45D5325A9478F880E351484F62FEF15F336462E449CA0C03C873D589C2` |

PLY 3.11 official source distribution SHA-256: `00C7C1AAA88358B9C765B6D3000C6EEC0BA42ABCA5351B095321AEF446081DA3`.

## Native binary component inventory

The v0.3.32 Windows Wheel inspection found 52 `.pyd`, `.dll`, `.lib`, and `.exe` files grouped into seven identifiable component groups. None of these binaries is stored in the GridWise Campus source-only repository.

| Component group | Evidence in audited Wheels | License / notice evidence | Source-only release treatment | Status |
|---|---|---|---|---|
| HiGHS/highspy extension | `highspy` CPython extension and associated HiGHS code | HiGHS MIT plus the component notices listed above | Not bundled; installed independently by the user | REVIEW_REQUIRED if redistributed |
| Microsoft Visual C++ Runtime | Byte-identical `msvcp140` DLLs in the highspy and NumPy Wheels; audited DLL version 14.40.33810.0 | The Wheels did not provide Microsoft redistribution terms | Not bundled; source-only release avoids acting as redistributor | REVIEW_REQUIRED; unresolved |
| NumPy compiled extensions and import libraries | NumPy `.pyd` and `.lib` files | NumPy main and component license set | Not bundled | REVIEW_REQUIRED if redistributed |
| OpenBLAS | `numpy.libs/libscipy_openblas64_*.dll` | BSD-3-Clause; OpenBLAS Project copyright notices in NumPy's license set | Not bundled | CONDITIONAL if redistributed |
| LAPACK within OpenBLAS | Identified by NumPy's binary license section | BSD-3-Clause-Open-MPI; University of Tennessee, University of California Berkeley, and University of Colorado Denver notices | Not bundled | CONDITIONAL if redistributed |
| GCC Fortran runtime within OpenBLAS | Identified by NumPy's binary license section | `GPL-3.0-or-later WITH GCC-exception-3.1`; Free Software Foundation | Not bundled; the Runtime Library Exception is material and must accompany any future redistribution analysis | CONDITIONAL if redistributed |
| setuptools Windows launchers | Eight launcher `.exe` files in the audited setuptools Wheel | setuptools MIT license and its vendored notices | Build-tool artifacts are not bundled | REVIEW_REQUIRED if bundled |

The highspy notice also declares six source-component families (AMD, CLI11, METIS, pdqsort, rcm, and zstr). The audit did not prove that every one of them is linked into every distributed binary configuration; their upstream notices must nevertheless be preserved if a future GridWise distribution includes an artifact containing them.

## NOTICE decision

No project `NOTICE` file is created at this stage.

- Apache License 2.0 does not require a non-ASF project to invent an empty `NOTICE` file.
- The source-only repository does not incorporate third-party source or binaries whose upstream `NOTICE` file has been copied into the project.
- The required third-party inventory and conditional redistribution duties are recorded here.
- If a future release incorporates third-party code, binaries, or an upstream `NOTICE`, the release contents must be re-audited and an appropriate project `NOTICE` must be added without removing required upstream attributions.

## Remaining review items

1. The Microsoft Visual C++ Runtime redistribution basis for the audited highspy and NumPy Wheels is not established for GridWise Campus. This is not a blocker for the defined source-only release because those DLLs are not bundled; it is a blocker for any future offline or binary bundle until resolved.
2. PLY's Wheel did not carry its license text. The official sdist supplied the complete BSD-3-Clause text, which must be retained if PLY is redistributed.
3. The audited setuptools Wheel contains vendored components under licenses beyond its top-level MIT metadata. It remains an external build tool in the source-only release.
4. This inventory does not certify package authorship, reproducible builds, signatures, or legal compliance. It records the evidence obtained in the technical audit.
