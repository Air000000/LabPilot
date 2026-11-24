# NOTEARS-CPU-001

Make the frozen working-source NOTEARS linear solver usable as a headless library and CLI.
Keep its mathematical objective, public function signature, default parameters, and existing CLI arguments.
Move the added top-level visualization into an explicitly invoked reusable helper:
`notears.visualization.plot_linear_comparison(W_true, W_est, B_true, output_dir)`.
The helper saves the three existing PNGs and closes figures; by default it must not call interactive show.
Keep visualization available from the existing explicit demo execution path. Importing notears.linear
must not require matplotlib/networkx/seaborn, start training, create output files or open windows.

Expose --max_iter, --h_tol, --rho_max, --w_threshold in bin/notears_linear and pass them to
notears_linear with unchanged defaults. Preserve --lambda1, --loss_type, --W_path and the positional CSV.
Develop tests and update necessary usage documentation. Do not change nonlinear.py, data, dependencies,
solver formulas, evaluator files or protected baseline metadata. Return a reviewable multi-file change.
