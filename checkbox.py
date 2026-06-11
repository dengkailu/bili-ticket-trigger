"""
终端复选框选择器 — 无外部依赖

用法:
  selector = CheckboxSelector(["邓恺璐  135****", "王龙  158****"], max_select=2)
  selected = selector.run()
  → [0, 1]  # 被选中项的索引
"""

import sys
import os
import termios
import tty
import select


class CheckboxSelector:
    def __init__(self, items: list[str], max_select: int = 1,
                 prompt: str = "选择购票人"):
        self.items = items
        self.max_select = max_select
        self.prompt = prompt
        self.selected = set()  # 当前选中的索引
        self.cursor = 0        # 光标位置

    def _getch(self):
        """读取单个按键 (支持方向键)"""
        if select.select([sys.stdin], [], [], 0.1)[0]:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                rest = sys.stdin.read(2)
                if rest == '[A': return 'UP'
                if rest == '[B': return 'DOWN'
            return ch
        return None

    def _draw(self):
        """绘制复选框列表"""
        lines = [f"\r\033[K  {self.prompt} (空格=勾选, 回车=确认, 最多{self.max_select}人)"]
        for i, item in enumerate(self.items):
            mark = "✓" if i in self.selected else " "
            cursor = " →" if i == self.cursor else "  "
            lines.append(f"\r\033[K{cursor} [{mark}] {item}")
        lines.append(f"\r\033[K  已选: {len(self.selected)}/{self.max_select}")
        # 移动光标到顶部
        sys.stdout.write(f"\033[{len(lines)}A")
        for line in lines:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def run(self) -> list[int]:
        """运行选择器, 返回选中项索引列表"""
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write("\033[?25l")  # 隐藏光标
            self._draw()

            while True:
                ch = self._getch()
                if ch == 'UP':
                    self.cursor = (self.cursor - 1) % len(self.items)
                    self._draw()
                elif ch == 'DOWN':
                    self.cursor = (self.cursor + 1) % len(self.items)
                    self._draw()
                elif ch in (' ', '\n', '\r'):
                    if ch == ' ':
                        if self.cursor in self.selected:
                            self.selected.discard(self.cursor)
                        elif len(self.selected) < self.max_select:
                            self.selected.add(self.cursor)
                        self._draw()
                    else:  # Enter
                        if len(self.selected) == 0 and self.max_select == 1:
                            self.selected = {self.cursor}
                        if len(self.selected) > 0:
                            return sorted(self.selected)
                elif ch == '\x03':  # Ctrl+C
                    return []
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write("\033[?25h")  # 显示光标
            sys.stdout.write("\n")


def checkbox_prompt(items: list[str], max_select: int = 1,
                    prompt: str = "选择") -> list[int]:
    """便捷函数"""
    s = CheckboxSelector(items, max_select, prompt)
    return s.run()
