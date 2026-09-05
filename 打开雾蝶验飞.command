#!/bin/bash
# 单只雾蝶 · 飞行验飞（独立原型页，不接大厅、不接后端）
cd "$(dirname "$0")/前端" || exit 1
open "http://localhost:5199/butterfly.html"
npx vite --port 5199 --strictPort
