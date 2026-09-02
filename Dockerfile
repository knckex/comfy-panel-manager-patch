# ComfyUI-Managerのsecurity_levelを"weak"にするだけの、ごく薄い派生イメージ。
# モデルは一切焼き込まない（今まで通りgenerate.php側で配置する）。
#
# 【要検証】ComfyUIの実体（/ComfyUI or /workspace/ComfyUI）がイメージのビルド時点で
# 既に存在するのか、それともコンテナ起動時に初めて展開されるのかが未確認。
# もし起動時展開なら、この単純なCOPYでは効かず、start.sh等のentrypointを
# 上書きして「起動直後・ComfyUI本体が起動する前」にconfig.iniを書き込む方式に
# 変更する必要がある（原イメージのentrypointの中身を見てから調整すること）。
FROM runpod/comfyui:latest

# ComfyUI-Managerの設定ファイルは、バージョンによって置き場所が違う。
# 存在する場所だけ上書きされれば良いので、可能性のある全パスに配っておく
COPY config.ini /workspace/ComfyUI/user/default/ComfyUI-Manager/config.ini
COPY config.ini /workspace/ComfyUI/user/__manager/config.ini
COPY config.ini /ComfyUI/user/default/ComfyUI-Manager/config.ini
COPY config.ini /ComfyUI/user/__manager/config.ini
