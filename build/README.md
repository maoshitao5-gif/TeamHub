# 应用图标

请将应用图标文件放置在此目录：

## 所需图标文件

1. **icon.ico** - Windows 图标（256x256，推荐使用工具生成）
2. **icon.png** - Linux 图标（512x512 PNG）
3. **icon.icns** - macOS 图标（使用 iconutil 生成）

## 图标规格

- **Windows (.ico)**
  - 推荐尺寸：256x256
  - 格式：ICO
  - 可包含多个尺寸（16, 32, 48, 128, 256）

- **Linux (.png)**
  - 推荐尺寸：512x512
  - 格式：PNG
  - 背景：透明

- **macOS (.icns)**
  - 推荐尺寸：512x512 或 1024x1024
  - 格式：ICNS
  - 使用 `iconutil` 或在线工具生成

## 图标生成工具

### Windows ICO
- https://icoconvert.com/
- https://convertio.co/png-ico/

### macOS ICNS
```bash
mkdir icon.iconset
cp icon_512x512.png icon.iconset/icon_256x256@2x.png
cp icon_512x512.png icon.iconset/icon_512x512.png
iconutil -c icns icon.iconset
```

### 在线工具
- https://cloudconvert.com/png-to-icns
- https://anyconv.com/png-to-icns-converter/

## 临时占位

如果暂时没有准备好图标，Electron Builder 会使用默认图标。
建议尽快添加自定义图标以提升应用的专业性。
