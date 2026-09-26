on findAccount(wanted)
  tell application "Messages"
    repeat with a in accounts
      try
        if ((service type of a) as text) is wanted and (enabled of a) then return a
      end try
    end repeat
  end tell
  return missing value
end findAccount

on run argv
  set theNumber to item 1 of argv
  set dryRun to (count of argv) < 2
  set svc to findAccount("SMS")
  if svc is missing value then set svc to findAccount("iMessage")
  if svc is missing value then error "no usable SMS/iMessage account"
  tell application "Messages"
    set p to participant theNumber of svc
    set svcName to service type of svc as text
    if dryRun then return "DRY RUN ok: would send via " & svcName & " to " & theNumber
    send (item 2 of argv) to p
  end tell
  return "sent via " & svcName
end run
