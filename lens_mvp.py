"""
Antigravity Optic Sim MVP
PyInstaller --onefile --windowed 対応版

ビルドコマンド:
    py -m PyInstaller --onefile --windowed lens_mvp.py
"""
import sys
import os
import math
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
import multiprocessing


# ===========================================================
# PyInstaller --onefile / 通常実行 両対応のベースパス取得
# ===========================================================
def get_base_dir() -> str:
    """
    実行環境に応じた基準ディレクトリを返す。
    - PyInstaller --onefile : sys._MEIPASS (一時展開先)
    - 通常の .py 実行       : このスクリプトのディレクトリ
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()


def resource_path(relative_path: str) -> str:
    """
    add-data で同梱した外部ファイルへの絶対パスを返す。
    相対パスで open() する代わりにこの関数を使う。

    例) resource_path("assets/icon.ico")
    """
    return os.path.join(BASE_DIR, relative_path)


# ===========================================================
# Windows DPI 対応 (高解像度ディスプレイでぼやけを防ぐ)
# ===========================================================
def enable_dpi_awareness() -> None:
    """Windows でのみ DPI スケーリングを有効化する。"""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        # Per-monitor DPI Aware V2 (Win10 1703+)
        windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # 失敗しても続行


# ===========================================================
# 光線追跡コア
# ===========================================================
class Ray:
    def __init__(self, x: float, y: float, dx: float, dy: float) -> None:
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.history: list[tuple[float, float]] = [(x, y)]


def intersect_sphere(
    ray: Ray, Cx: float, R: float, vertex_x: float
) -> float | None:
    """球面との交差パラメータ t を返す。交差なし → None"""
    if abs(R) > 1e6:  # 平面
        if ray.dx == 0:
            return None
        t = (vertex_x - ray.x) / ray.dx
        return t if t > 1e-6 else None

    Rad = abs(R)
    B = ray.dx * (ray.x - Cx) + ray.dy * ray.y
    C = (ray.x - Cx) ** 2 + ray.y ** 2 - Rad ** 2
    D = B * B - C

    if D < 0:
        return None

    sqrtD = math.sqrt(D)
    candidates: list[float] = []
    for t in (-B - sqrtD, -B + sqrtD):
        if t > 1e-6:
            hit_x = ray.x + ray.dx * t
            if (hit_x - Cx) * R <= 1e-6:
                candidates.append(t)

    return min(candidates) if candidates else None


def get_normal(x: float, y: float, Cx: float) -> tuple[float, float]:
    Nx, Ny = x - Cx, y
    L = math.hypot(Nx, Ny)
    if L == 0:
        return 1.0, 0.0
    return Nx / L, Ny / L


def refract(
    dx: float, dy: float,
    Nx: float, Ny: float,
    n1: float, n2: float,
) -> tuple[float | None, float | None]:
    """スネルの法則。全反射の場合 (None, None) を返す。"""
    mu = n1 / n2
    cosI = -(dx * Nx + dy * Ny)
    if cosI < 0:
        Nx, Ny = -Nx, -Ny
        cosI = -cosI

    sin2_I = max(0.0, 1.0 - cosI * cosI)
    sin2_T = mu * mu * sin2_I

    if sin2_T > 1.0:
        return None, None  # 全反射

    cosT = math.sqrt(1.0 - sin2_T)
    out_dx = mu * dx + (mu * cosI - cosT) * Nx
    out_dy = mu * dy + (mu * cosI - cosT) * Ny

    L = math.hypot(out_dx, out_dy)
    if L == 0:
        return None, None
    return out_dx / L, out_dy / L


# ===========================================================
# GUI
# ===========================================================
class LensSimulator:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Antigravity Optic Sim MVP")
        self.root.geometry("1100x700")

        # ---- レイアウト ----
        left_frame = ttk.Frame(root, padding=10, width=300)
        left_frame.pack(side=tk.LEFT, fill=tk.Y)

        self.canvas = tk.Canvas(root, bg="#1E1E1E")
        self.canvas.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # ---- スライダー群 ----
        self.create_slider(left_frame, "R1 (Front Curvature, + is Convex)", "r1", 100, -300, 300)
        self.create_slider(left_frame, "R2 (Back Curvature, - is Convex)", "r2", -100, -300, 300)
        self.create_slider(left_frame, "Thickness (d)", "d", 30, 5, 200)
        self.create_slider(left_frame, "Refractive Index (n)", "n", 1.5, 1.0, 2.5, 0.01)
        self.create_slider(left_frame, "Beam Height", "bw", 80, 10, 300)
        self.create_slider(left_frame, "Number of Rays", "nr", 21, 3, 101, 2)
        self.create_slider(left_frame, "Light Source Pos X", "sx", -250, -500, -10)
        self.create_slider(left_frame, "Incident Angle (deg)", "ang", 0, -45, 45, 0.1)

        ttk.Label(
            left_frame,
            text=(
                "* Move sliders to see real-time Ray Tracing.\n"
                "* R=0 forces planar surface.\n"
                "* Missing rays imply Total Internal Reflection\n"
                "  or missing the pupil."
            ),
            wraplength=250,
        ).pack(pady=20)

        self._update_job: str | None = None
        self.root.bind("<Configure>", self.delayed_update)
        self.update()

    # ---- ウィジェットヘルパー ----
    def create_slider(
        self,
        parent: ttk.Frame,
        label: str,
        var_name: str,
        default: float,
        min_v: float,
        max_v: float,
        res: float = 1.0,
    ) -> None:
        ttk.Label(parent, text=label).pack(anchor=tk.W, pady=(10, 0))
        var: tk.Variable = (
            tk.IntVar(value=int(default)) if var_name == "nr"
            else tk.DoubleVar(value=default)
        )
        setattr(self, var_name + "_var", var)
        ttk.Scale(
            parent, variable=var, from_=min_v, to=max_v,
            command=self.delayed_update,
        ).pack(fill=tk.X)
        ttk.Label(parent, textvariable=var).pack(anchor=tk.E)

    # ---- 更新制御 ----
    def delayed_update(self, *_args: object) -> None:
        if self._update_job is not None:
            self.root.after_cancel(self._update_job)
        self._update_job = self.root.after(20, self.update)

    def update(self, *_args: object) -> None:
        self._update_job = None
        self.canvas.delete("all")

        # ---- パラメータ取得 ----
        R1 = float(self.r1_var.get())  # type: ignore[attr-defined]
        if abs(R1) < 1:
            R1 = 1e9 if R1 >= 0 else -1e9
        R2 = float(self.r2_var.get())  # type: ignore[attr-defined]
        if abs(R2) < 1:
            R2 = 1e9 if R2 >= 0 else -1e9

        d   = float(self.d_var.get())    # type: ignore[attr-defined]
        n   = float(self.n_var.get())    # type: ignore[attr-defined]
        bw  = float(self.bw_var.get())   # type: ignore[attr-defined]
        nr  = int(self.nr_var.get())     # type: ignore[attr-defined]
        sx  = float(self.sx_var.get())   # type: ignore[attr-defined]
        ang_deg = float(self.ang_var.get())  # type: ignore[attr-defined]

        Cx1 = R1
        Cx2 = d + R2

        # ---- スケーリング ----
        scale = 2.0
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1:
            w = 800
        if h <= 1:
            h = 700

        offset_x = w // 2 - int((d * scale) // 2)
        offset_y = h // 2

        # ---- グリッド ----
        self.canvas.create_line(0, offset_y, w, offset_y, fill="#3A3A3A", dash=(4, 4))
        self.canvas.create_line(offset_x, 0, offset_x, h, fill="#3A3A3A", dash=(4, 4))

        # ---- レンズメーカー方程式 ----
        if abs(R1) < 1e8 and abs(R2) < 1e8:
            P = (n - 1) * (1 / R1 - 1 / R2 + ((n - 1) * d) / (n * R1 * R2))
            f = 1 / P if P != 0 else 0.0
        elif abs(R1) > 1e8 and abs(R2) < 1e8:
            f = -R2 / (n - 1)
        elif abs(R2) > 1e8 and abs(R1) < 1e8:
            f = R1 / (n - 1)
        else:
            f = 0.0

        self.canvas.create_text(
            20, 20,
            text=f"Lens Maker EFL: {f:.2f} mm",
            fill="white",
            anchor=tk.NW,
            font=("Arial", 12),
        )

        # ---- レンズ形状描画 ----
        steps = 60
        y_max = bw * 1.5
        y_max = min(y_max, abs(R1) * 0.98 if abs(R1) < 1e6 else y_max)
        y_max = min(y_max, abs(R2) * 0.98 if abs(R2) < 1e6 else y_max)

        front_points: list[tuple[float, float]] = []
        for i in range(steps + 1):
            y = -y_max + (2 * y_max * i / steps)
            if abs(R1) > 1e6:
                x = 0.0
            else:
                rad = R1 ** 2 - y ** 2
                x = Cx1 - (1 if R1 > 0 else -1) * math.sqrt(rad) if rad >= 0 else 0.0
            front_points.append((offset_x + x * scale, offset_y + y * scale))

        back_points: list[tuple[float, float]] = []
        for i in range(steps + 1):
            y = y_max - (2 * y_max * i / steps)
            if abs(R2) > 1e6:
                x = d
            else:
                rad = R2 ** 2 - y ** 2
                x = Cx2 - (1 if R2 > 0 else -1) * math.sqrt(rad) if rad >= 0 else d
            back_points.append((offset_x + x * scale, offset_y + y * scale))

        try:
            self.canvas.create_polygon(
                front_points + back_points,
                fill="#2C3E50", outline="#3498DB", width=2,
            )
        except Exception:
            pass

        # ---- 光線追跡 ----
        rad_ang = math.radians(ang_deg)
        dir_x = math.cos(rad_ang)
        dir_y = math.sin(rad_ang)

        rays: list[Ray] = [
            Ray(sx, (-bw / 2 + i * (bw / (nr - 1))) if nr > 1 else 0.0, dir_x, dir_y)
            for i in range(nr)
        ]

        for ray in rays:
            t1 = intersect_sphere(ray, Cx1, R1, 0.0)
            if t1 is not None and t1 > 0:
                ray.x += ray.dx * t1
                ray.y += ray.dy * t1
                ray.history.append((ray.x, ray.y))

                Nx, Ny = ((-1.0, 0.0) if abs(R1) > 1e6
                          else get_normal(ray.x, ray.y, Cx1))
                odx, ody = refract(ray.dx, ray.dy, Nx, Ny, 1.0, n)

                if odx is not None:
                    ray.dx, ray.dy = odx, ody

                    t2 = intersect_sphere(ray, Cx2, R2, d)
                    if t2 is not None and t2 > 0:
                        ray.x += ray.dx * t2
                        ray.y += ray.dy * t2
                        ray.history.append((ray.x, ray.y))

                        Nx2, Ny2 = ((-1.0, 0.0) if abs(R2) > 1e6
                                    else get_normal(ray.x, ray.y, Cx2))
                        odx2, ody2 = refract(ray.dx, ray.dy, Nx2, Ny2, n, 1.0)

                        if odx2 is not None:
                            ray.dx, ray.dy = odx2, ody2
                            ray.x += ray.dx * 1500
                            ray.y += ray.dy * 1500
                            ray.history.append((ray.x, ray.y))
            else:
                ray.x += ray.dx * 1500
                ray.y += ray.dy * 1500
                ray.history.append((ray.x, ray.y))

            for i in range(len(ray.history) - 1):
                x1 = offset_x + ray.history[i][0] * scale
                y1 = offset_y + ray.history[i][1] * scale
                x2 = offset_x + ray.history[i + 1][0] * scale
                y2 = offset_y + ray.history[i + 1][1] * scale
                self.canvas.create_line(x1, y1, x2, y2, fill="#E67E22", width=1.5)


# ===========================================================
# エントリーポイント
# ===========================================================
def main() -> None:
    enable_dpi_awareness()

    root = tk.Tk()
    app = LensSimulator(root)
    root.mainloop()


if __name__ == "__main__":
    # PyInstaller --onefile + multiprocessing 対応
    # Windows では freeze_support() が必須
    multiprocessing.freeze_support()

    try:
        main()
    except Exception as exc:  # noqa: BLE001
        # --windowed ビルドではコンソールがないためメッセージボックスでエラーを表示
        error_text = traceback.format_exc()
        try:
            root_err = tk.Tk()
            root_err.withdraw()
            messagebox.showerror(
                "Antigravity Optic Sim - Fatal Error",
                f"予期しないエラーが発生しました:\n\n{error_text}",
            )
            root_err.destroy()
        except Exception:
            pass
        sys.exit(1)
