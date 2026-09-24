# Antigravity-র নিরাপত্তা সেটিংস: সুপারিশ

Skill আর rule আসলে মডেলকে দেওয়া নির্দেশ, আর মডেল কখনো কখনো নির্দেশ উপেক্ষা করে। কিন্তু Antigravity-র নিজের permission সেটিংস মডেল উপেক্ষা করতে পারে না। তাই বিপজ্জনক কাজ আটকানোর সবচেয়ে নির্ভরযোগ্য উপায় এই সেটিংস।

নিচের সব নিয়ম Antigravity-র অফিসিয়াল ডকুমেন্টেশনের সিনট্যাক্সে লেখা ([Permissions](https://antigravity.google/docs/permissions/))। আমি নিজে Antigravity অ্যাপে এগুলো চালিয়ে দেখতে পারিনি। তাই যোগ করার পর একটা নিরীহ কমান্ড দিয়ে পরীক্ষা করে নিন। যেমন একটা খালি test ফোল্ডার বানিয়ে এজেন্টকে বলুন সেটা মুছতে, তারপর দেখুন অনুমতি চায় কি না।

## ১. Preset বাছাই

Settings → General → Permission Settings-এ যান।

| আপনার কম্পিউটার | সুপারিশ | কেন |
|---|---|---|
| macOS / Linux | **Default** | কমান্ড একটা sandbox-এ চলে, যেখান থেকে শুধু workspace আর temp ফোল্ডার দেখা যায়। `.env` আর `~/.ssh` sandbox থেকে দেখা যায় না। |
| Windows | **Request Review** | Windows এখনো পুরোনো permission ব্যবস্থায় চলে, সেখানে sandbox নেই। তাই প্রতিটা কমান্ড আপনার অনুমতি নিয়ে চলুক। |

**Turbo কখনো ব্যবহার করবেন না।** D: ড্রাইভ মুছে যাওয়ার ঘটনাটা Turbo মোডেই ঘটেছিল।

Windows-এ কিছু নিরীহ কমান্ড Allow list-এ রাখলে বারবার অনুমতি চাওয়া কমবে (নিচে ৪ নম্বর অংশ দেখুন)।

## ২. Deny list: এগুলো সবসময় বন্ধ থাকবে

`<HOME>`-এর জায়গায় আপনার হোম ফোল্ডার লিখুন:
- macOS-এ `/Users/<নাম>`
- Linux-এ `/home/<নাম>`
- Windows-এ `/Users/<নাম>`। Windows-এ Antigravity নিজেই `C:` অংশ বাদ দেয় আর `\`-কে `/` বানিয়ে নেয়।

```
command(sudo)
command(format)
command(diskpart)
command(mkfs)
command(dd)
command(shutdown)
command(reboot)
command(Format-Volume)
command(Clear-Disk)
command(git push --force)
command(git push -f)
write_file(.git/)
read_file(<HOME>/.ssh)
write_file(<HOME>/.gemini/config/hooks.json)
write_file(<HOME>/.gemini/antigravity/mcp_config.json)
```

শেষ দুটো নিয়ম prompt injection ঠেকানোর জন্য। এগুলো থাকলে কোনো ওয়েবপেজ বা ক্ষতিকর ফাইল এজেন্টকে দিয়ে আপনার hooks বন্ধ করাতে বা ক্ষতিকর MCP কনফিগ বসাতে পারবে না। এই ফাইলগুলো আপনি নিজে হাতে বদলাতে পারবেন।

## ৩. Ask list: এগুলোর আগে সবসময় অনুমতি চাইবে

```
command(rm)
command(rmdir)
command(rd)
command(del)
command(erase)
command(ri)
command(Remove-Item)
command(git reset)
command(git clean)
command(git restore)
command(git rebase)
command(git stash drop)
command(git stash clear)
command(git branch -D)
command(git push)
command(npm publish)
command(terraform)
command(kubectl)
command(firebase deploy)
command(curl)
command(wget)
command(Invoke-WebRequest)
command(iwr)
command(Invoke-RestMethod)
command(irm)
read_file(.env)
write_file(<HOME>/.gemini)
```

আরও কড়া নিয়ম চাইলে এগুলোও যোগ করতে পারেন (ঐচ্ছিক, তবে বেশি বার অনুমতি চাইবে):

```
command(mv)
command(move)
command(Move-Item)
command(git checkout)
command(git commit)
command(docker)
command(gcloud)
```

Antigravity-তে নিয়মের অগ্রাধিকার হলো Deny > Ask > Allow। তাই কোনো কমান্ড Ask list-এ থাকলে সেটা Allow list-এ থাকলেও অনুমতি চাইবে।

## ৪. Allow list: শুধু পড়ার নিরীহ কমান্ড

এই অংশটা বিশেষ করে Windows-এর Request Review মোডের জন্য, যাতে বারবার অনুমতি চাওয়া কমে।

```
command(git status)
command(git diff)
command(git log)
command(git show)
command(ls)
command(dir)
command(pwd)
command(Get-ChildItem)
command(Get-Location)
```

**`cat`, `type` আর `Get-Content` ইচ্ছে করেই এই তালিকায় রাখিনি।** নভেম্বর ২০২৫-এর আক্রমণে Gemini ঠিক `cat` দিয়েই `.env` পড়ে ফেলেছিল। তাই এগুলো প্রতিবার অনুমতি নিয়ে চলুক।

## ৫. ব্রাউজার

- দরকার না হলে ব্রাউজার টুল বন্ধ রাখুন: User Settings → Browser → Browser Tools।
- Browser URL Allowlist বা Denylist-এ দেখুন webhook.site বা এ ধরনের "request catcher" সাইট আছে কি না। থাকলে সরিয়ে দিন। নভেম্বর ২০২৫-এর রিপোর্টে webhook.site ডিফল্ট allowlist-এ পাওয়া গিয়েছিল; এখন সেটা বদলে থাকতে পারে, তাই একবার নিজে দেখে নিন।
- ব্রাউজারে লগইন, পেমেন্ট আর 2FA নিজে করুন। এজেন্টকে পাসওয়ার্ড দেবেন না।

## ৬. মডেল আর মোড

- কোডিং আর বহু-ধাপের কাজে Gemini 3.8 Flash-এর **Medium** thinking রাখুন, আর কঠিন কাজে **High**। Google নিজেই জটিল কোড আর এজেন্টের কাজে Medium সুপারিশ করে। Low শুধু দ্রুত প্রশ্নোত্তরের জন্য।
- খুব গুরুত্বপূর্ণ বা ঝুঁকির কাজে মডেল তালিকা থেকে **Claude Opus 4.6 (thinking)** বেছে নিতে পারেন। Free, Google AI Plus, Pro আর Ultra প্ল্যানে আছে, Enterprise-এ নেই। এই skill সেখানেও কাজ করবে।
- বড় কাজে **Planning mode** ব্যবহার করুন। এতে কাজ শুরুর আগেই পরিকল্পনা (Implementation Plan) দেখে সংশোধন করতে পারবেন।
- কাজ শুরুর আগে git commit বা ব্যাকআপ রাখুন।
