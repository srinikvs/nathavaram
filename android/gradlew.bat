@rem Minimal Gradle wrapper launcher (gradle-wrapper.jar is downloaded by the build job).
@echo off
setlocal
set APP_HOME=%~dp0
set WRAPPER_JAR=%APP_HOME%gradle\wrapper\gradle-wrapper.jar

if not exist "%WRAPPER_JAR%" (
    echo gradle-wrapper.jar is missing. Run 'gradle wrapper --gradle-version 8.9' first. 1>&2
    exit /b 1
)

if defined JAVA_HOME (
    set JAVACMD=%JAVA_HOME%\bin\java.exe
) else (
    set JAVACMD=java.exe
)

"%JAVACMD%" -Xmx64m -Xms64m -Dorg.gradle.appname=gradlew -classpath "%WRAPPER_JAR%" org.gradle.wrapper.GradleWrapperMain %*
endlocal
