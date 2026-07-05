@echo off
echo ======================================
echo   ALONGSI - פריסה לשרת הייצור
echo ======================================
echo.
ssh -i "%USERPROFILE%\.ssh\alongsi" root@178.105.119.191 "cd /opt/alongsi && git pull origin master && systemctl restart alongsi && echo === הפריסה הצליחה ==="
echo.
echo ======================================
echo   הושלם! ניתן לסגור חלון זה.
echo ======================================
pause
