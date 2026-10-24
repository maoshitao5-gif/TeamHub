use framework "Foundation"
use scripting additions

on executeProvision(projectRoot, taskArguments, payload)
    set task to current application's NSTask's alloc()'s init()
    task's setLaunchPath:(projectRoot & "/cloud_backend/venv/bin/python")
    task's setArguments:({projectRoot & "/cloud_backend/account_setup.py"} & taskArguments)
    task's setCurrentDirectoryPath:(projectRoot & "/cloud_backend")
    set taskEnvironment to current application's NSProcessInfo's processInfo()'s environment()'s mutableCopy()
    taskEnvironment's setObject:"1" forKey:"TEAMHUB_SANDBOX"
    taskEnvironment's setObject:("sqlite:///" & projectRoot & "/.mvp-sandbox/shared-server/cloud.db") forKey:"DATABASE_URL"
    taskEnvironment's setObject:(projectRoot & "/.mvp-sandbox/shared-server/storage") forKey:"STORAGE_DIR"
    task's setEnvironment:taskEnvironment
    set inputPipe to current application's NSPipe's pipe()
    set outputPipe to current application's NSPipe's pipe()
    set errorPipe to current application's NSPipe's pipe()
    task's setStandardInput:inputPipe
    task's setStandardOutput:outputPipe
    task's setStandardError:errorPipe
    task's |launch|()
    if payload is not missing value then
        set inputData to current application's NSJSONSerialization's dataWithJSONObject:payload options:0 |error|:(missing value)
        inputPipe's fileHandleForWriting()'s writeData:inputData
    end if
    inputPipe's fileHandleForWriting()'s closeFile()
    set outputData to outputPipe's fileHandleForReading()'s readDataToEndOfFile()
    set errorData to errorPipe's fileHandleForReading()'s readDataToEndOfFile()
    task's waitUntilExit()
    set response to current application's NSJSONSerialization's JSONObjectWithData:outputData options:0 |error|:(missing value)
    if response is missing value then error "账号工具未返回有效结果，请确认项目的云端Python环境完整。"
    if ((response's objectForKey:"ok") as boolean) is false then error ((response's objectForKey:"error") as text)
    if task's terminationStatus() is not 0 then error "账号开通没有完成，请重新检查输入。"
    return response
end executeProvision

on run
    set launcherPath to POSIX path of (path to me)
    set projectRoot to do shell script "/usr/bin/dirname " & quoted form of launcherPath
    set accountPassword to ""
    set confirmPassword to ""
    set payload to missing value
    try
        set statusInfo to my executeProvision(projectRoot, {"--status"}, missing value)
        set hasAdmin to (statusInfo's objectForKey:"has_admin") as boolean
        if hasAdmin then
            set defaultRole to {"普通成员"}
        else
            set defaultRole to {"系统管理员（首次使用）"}
        end if
        activate
        set roleChoice to choose from list {"系统管理员（首次使用）", "普通成员"} with title "开通云端账号" with prompt "这是本机维护入口。首次使用先创建管理员；后续可开通普通成员。" default items defaultRole
        if roleChoice is false then return
        set accountRole to "member"
        if item 1 of roleChoice is "系统管理员（首次使用）" then set accountRole to "admin"
        set loginEmail to text returned of (display dialog "登录邮箱（作为账号名，不发送邮件）：" default answer "" buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "开通云端账号")
        set displayName to text returned of (display dialog "显示名称：" default answer "" buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "开通云端账号")
        if length of loginEmail > 254 or length of displayName > 100 then error "邮箱最多254字符，名称最多100字符。"
        set accountPassword to text returned of (display dialog "设置密码（至少8个字符）：" default answer "" buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "开通云端账号" with hidden answer)
        set passwordBytes to (current application's NSString's stringWithString:accountPassword)'s lengthOfBytesUsingEncoding:(current application's NSUTF8StringEncoding)
        if length of accountPassword < 8 or passwordBytes > 72 then error "密码至少8个字符，UTF-8编码不能超过72字节。"
        set confirmPassword to text returned of (display dialog "再次输入密码：" default answer "" buttons {"取消", "下一步"} default button "下一步" cancel button "取消" with title "开通云端账号" with hidden answer)
        if accountPassword is not confirmPassword then error "两次密码不一致，请重新开通。"
        set roleLabel to item 1 of roleChoice
        set roleDescription to "普通成员可使用团队资料库。"
        if accountRole is "admin" then set roleDescription to "系统管理员可管理账号和团队；受限资料仍需明确授权。"
        display dialog ("确认开通账号：" & return & loginEmail & return & displayName & return & "角色：" & roleLabel & return & roleDescription & return & "团队：MVP团队 / 组织资料") buttons {"取消", "确认开通"} default button "确认开通" cancel button "取消" with title "开通云端账号"
        set payload to current application's NSMutableDictionary's dictionary()
        payload's setObject:loginEmail forKey:"email"
        payload's setObject:displayName forKey:"name"
        payload's setObject:accountPassword forKey:"password"
        payload's setObject:accountRole forKey:"role"
        set accountResult to my executeProvision(projectRoot, {}, payload)
        set accountPassword to ""
        set confirmPassword to ""
        payload's removeObjectForKey:"password"
        display dialog ("账号已开通。" & return & "登录邮箱：" & ((accountResult's objectForKey:"email") as text) & return & "请在云端资料库或管理后台使用刚设置的密码登录。") buttons {"完成"} default button "完成" with title "开通完成"
    on error errorMessage number errorNumber
        set accountPassword to ""
        set confirmPassword to ""
        if payload is not missing value then payload's removeObjectForKey:"password"
        if errorNumber is not -128 then
            activate
            display alert "账号未开通" message errorMessage as critical giving up after 20
        end if
    end try
end run
