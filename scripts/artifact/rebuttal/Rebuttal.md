# Details of experiment for the rebuttal

**(A-Q1, A-Q3, C) Three kinds of evidence are generated from the same unsoundness report: Why did you choose three kinds of evidence (not two or four), and is there a risk that they may agree while being wrong, since they derive from the same source?**

| Tool | Reasoned | Pattern | Boundary | Trace | Revised |
|---|---:|---:|---:|---:|---:|
| WALA | 38 | 3 | 2 | 1 | 6 (16%) |
| OPAL | 44 | 3 | 5 | 0 | 8 (18%) |
| Soot | 69 | 10 | 2 | 3 | 15 (22%) |
| **Total** | **151** | **16** | **9** | **4** | **29 (19%)** |



**(C-Q1) What independent evidence shows that grouped boundaries actually share the representative's root cause?**

| Group size | Grouped boundaries | Share | Samples | Match | Mismatch | Agreement |
|---|---:|---:|---:|---:|---:|---:|
| Large (>20) | 128 | 65.3% | 29 | 21 | 8 | 72% |
| Medium (6–19) | 43 | 21.9% | 11 | 11 | 0 | 100% |
| Small (2–5) | 16 | 8.2% | 5 | 5 | 0 | 100% |
| Size 1 | 9 | 4.6% | 9 | 9 | 0 | 100% |
| **All** | **196** | **100%** | **54** | **46** | **8** | **85%** |


**(A-Q2, C-Q2) The methodology for assessing RQ3 may not produce accurate results due to the use of group membership to approximate coverage, and due to groups being determined by the full program set. Could a more direct assessment be performed?**

| tool  | cases_total | app_cases_selected | refactored | not_possible | not_possible_direct | not_possible_failed_tests |
|-------|------------:|---------------:|-----------:|-------------:|--------------------:|--------------------------:|
| wala  | 22          | 12             | 5          | 7            | 6                   | 1                         |
| opal  | 29          | 13             | 9          | 4            | 3                   | 1                         |
| soot  | 46          | 22             | 16         | 6            | 6                   | 0                         |
| TOTAL | 97         | **47**         | **30**     | 17           | 15                  | 2                         |
