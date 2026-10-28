# ck-image · 图片导入测试生成器

Python 3.9+ 与 Pillow 10+；标准库、pathlib，无联网代码。面向 macOS 13+ Apple Silicon/Intel，同时支持 Windows。实际平台验收见交付报告。

整个 ck-image 目录放到 ~/.agents/skills/，在 Codex 新对话中输入 $ck-image。若当前会话尚未刷新技能列表，重新打开会话。首次生成询问保存位置。

```text
$ck-image 生成500张测试图片
$ck-image 生成2000张不同尺寸的PNG图片，全部正常
$ck-image 生成1000张用于软件导入测试的混合格式图片
```

命令行（替换路径）：

```sh
python3 ~/.agents/skills/ck-image/scripts/generate.py --show-config
python3 ~/.agents/skills/ck-image/scripts/generate.py --output /path/to/test-images --remember --count 10 --formats JPG --ratios 100,0,0
```

配置：macOS ~/Library/Application Support/ck-image/config.json，Windows %LOCALAPPDATA%/ck-image/config.json，其他平台 $XDG_CONFIG_HOME/ck-image/config.json 或 ~/.config/ck-image/config.json。--config 可指定独立测试配置，不污染正式设置。

若缺 Pillow，脚本只报告，不安装。用户可自行选择隔离环境（pip 可能联网，只有用户执行或授权后才安装）：

```sh
python3 -m venv ~/.local/share/ck-image/venv
~/.local/share/ck-image/venv/bin/python -m pip install -r ~/.agents/skills/ck-image/requirements.txt
~/.local/share/ck-image/venv/bin/python ~/.agents/skills/ck-image/scripts/generate.py --help
```

AVIF 仅当实际编解码探测成功才启用。基础格式缺编码器时明确报错。边界含极小、超长宽比、透明、灰度、调色板、PNG 16 位灰度、JPEG CMYK，按格式选择属性。V1 GIF/TIFF 单帧，无复杂元数据/复杂编码、GUI 或受控并行。

目标大小是软目标，最多三个候选，尺寸不变；小尺寸/ICO/固定 BMP 不一定命中。随机种子复现同版本环境内容，不承诺跨编码器版本逐字节相同。

写入前按实际字节检查容量、可用空间，保留 2,000,000,000 字节及额外文件系统余量，为 CSV/JSON/TXT 预留 32 MiB。不能控制其他进程同时消耗磁盘；I/O 错误中止并尽力保存报告，不能保证断电/强杀后报告完整。不要并发启动多个生成任务。
