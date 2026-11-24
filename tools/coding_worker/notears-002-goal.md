# NOTEARS-CPU-002

Add early input and hyperparameter validation to the bound NOTEARS linear solver.
Keep the existing function signature and defaults, valid-input mathematical operations,
all three supported loss functions, and the existing CLI and headless plotting behavior.
Do not change solver formulas, dependencies, data, nonlinear.py or visualization/CLI source.

Validate before centering or invoking the optimizer:
- X must be a nonempty two-dimensional NumPy array of finite real numeric values.
  Reject empty rows/columns, other ranks, NaN/Inf, complex or text data.
- lambda1, h_tol and w_threshold must be finite nonnegative real scalars.
  Zero is valid for each; do not impose stricter positive-only rules.
- rho_max must be a finite real scalar strictly greater than 1. The solver starts rho at 1;
  a cap <= 1 otherwise skips its first optimization step.
- max_iter must be a positive integer, including NumPy integer scalars.
- Real scalar controls accept Python/NumPy real numbers. Reject booleans, numeric strings,
  complex values and nonfinite values; do not silently cast or clamp invalid controls.
- loss_type must remain one of l2, logistic or poisson.

For invalid inputs raise ValueError naming the offending argument (X, lambda1, max_iter,
h_tol, rho_max, w_threshold or loss_type). Do not require specific full error wording.
Keep valid single-sample/single-variable inputs supported. Do not silently change the
optimization objective, acyclicity constraint, updates, stopping rules or output threshold.

Add development tests and document the input contract. Return a reviewable change;
submit only when ready. Independent validation also runs the frozen CPU and plotting
regressions from NOTEARS-CPU-001; development tests alone are not acceptance.
