# comfy-panel-manager-patch

`runpod/comfyui:latest` をベースに、ComfyUI-Managerの`security_level`を`weak`にするだけの薄いイメージ。
モデルは一切含まない。GitHub Actionsが自動でビルドし、`ghcr.io/<owner>/comfy-panel-manager-patch:latest` にpushする。

comfy-panel（GPU管理パネル）の `IMAGE_NAME` 設定をこのイメージに切り替えて使う。
