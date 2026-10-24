on run
    set launcherPath to POSIX path of (path to me)
    set projectRoot to do shell script "/usr/bin/dirname " & quoted form of launcherPath
    set startupScript to projectRoot & "/scripts/mvp-dev.sh"
    try
        do shell script "/bin/bash " & quoted form of startupScript & " start-desktop"
    on error errorMessage
        activate
        display alert "TeamHub 未启动" message (errorMessage & return & return & "启动日志：" & projectRoot & "/.mvp-sandbox/logs-desktop.log") as critical giving up after 15
    end try
end run
