"""Add an ephemeral UI-test target and offline transport to a CI-only checkout.

The tracked Xcode project and App entry are not modified for distribution.
"""
from pathlib import Path


def prepare():
    project = Path('ios/KeibaAI/KeibaAI.xcodeproj')
    path = project / 'project.pbxproj'
    text = path.read_text()
    assert 'KeibaAIPhoneUITests' not in text
    ids = {name: f'F1A1{n:020X}' for n, name in enumerate(
        ['transport_ref', 'transport_build', 'test_ref', 'test_build', 'product', 'sources',
         'frameworks', 'resources', 'target', 'config_list', 'debug', 'release', 'proxy', 'dependency'], 1)}

    def add(section, content):
        nonlocal text
        marker = f'/* End {section} section */'
        assert text.count(marker) == 1
        text = text.replace(marker, content + '\n' + marker)

    add('PBXBuildFile', f'''{ids['transport_build']} = {{isa = PBXBuildFile; fileRef = {ids['transport_ref']}; }};
{ids['test_build']} = {{isa = PBXBuildFile; fileRef = {ids['test_ref']}; }};''')
    add('PBXFileReference', f'''{ids['transport_ref']} = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = ../tests/PhoneUITestTransport.swift; sourceTree = SOURCE_ROOT; }};
{ids['test_ref']} = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = ../tests/PhoneNavigationUITests.swift; sourceTree = SOURCE_ROOT; }};
{ids['product']} = {{isa = PBXFileReference; explicitFileType = wrapper.cfbundle; path = KeibaAIPhoneUITests.xctest; sourceTree = BUILT_PRODUCTS_DIR; }};''')
    add('PBXSourcesBuildPhase', f'''{ids['sources']} = {{isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = ({ids['test_build']},); runOnlyForDeploymentPostprocessing = 0; }};''')
    add('PBXFrameworksBuildPhase', f'''{ids['frameworks']} = {{isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};''')
    add('PBXResourcesBuildPhase', f'''{ids['resources']} = {{isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};''')
    add('PBXNativeTarget', f'''{ids['target']} = {{isa = PBXNativeTarget; name = KeibaAIPhoneUITests; productName = KeibaAIPhoneUITests; productReference = {ids['product']}; productType = "com.apple.product-type.bundle.ui-testing"; buildConfigurationList = {ids['config_list']}; buildPhases = ({ids['sources']},{ids['frameworks']},{ids['resources']},); dependencies = ({ids['dependency']},); }};''')
    settings = '''CODE_SIGNING_ALLOWED = NO; GENERATE_INFOPLIST_FILE = YES; IPHONEOS_DEPLOYMENT_TARGET = 27.0; PRODUCT_BUNDLE_IDENTIFIER = com.example.KeibaAIPhoneUITests; PRODUCT_NAME = "$(TARGET_NAME)"; SDKROOT = iphoneos; SUPPORTED_PLATFORMS = "iphoneos iphonesimulator"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = 1; TEST_TARGET_NAME = KeibaAI; LD_RUNPATH_SEARCH_PATHS = "$(inherited) @executable_path/Frameworks @loader_path/Frameworks";'''
    add('XCBuildConfiguration', '\n'.join(f'{ids[name.lower()]} = {{isa = XCBuildConfiguration; name = {name}; buildSettings = {{{settings}}}; }};' for name in ['Debug', 'Release']))
    add('XCConfigurationList', f'''{ids['config_list']} = {{isa = XCConfigurationList; buildConfigurations = ({ids['debug']},{ids['release']},); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; }};''')
    extra = f'''/* Begin PBXContainerItemProxy section */
{ids['proxy']} = {{isa = PBXContainerItemProxy; containerPortal = 21C9F92E307B28F200A81BC9; proxyType = 1; remoteGlobalIDString = 21C9F935307B28F200A81BC9; remoteInfo = KeibaAI; }};
/* End PBXContainerItemProxy section */
/* Begin PBXTargetDependency section */
{ids['dependency']} = {{isa = PBXTargetDependency; target = 21C9F935307B28F200A81BC9; targetProxy = {ids['proxy']}; }};
/* End PBXTargetDependency section */
'''
    text = text.replace('/* Begin PBXBuildFile section */', extra + '/* Begin PBXBuildFile section */')
    text = text.replace('21C9F968307B46C400A81BC9 /* ContentView.swift in Sources */,', f'{ids["transport_build"]},\n21C9F968307B46C400A81BC9 /* ContentView.swift in Sources */,')
    text = text.replace('21C9F935307B28F200A81BC9 /* KeibaAI */,', f'21C9F935307B28F200A81BC9 /* KeibaAI */,\n{ids["target"]},')
    path.write_text(text)
    transport=Path('ios/tests/PhoneUITestTransport.swift')
    fixture=Path('/tmp/bet-research-contract.json').read_text()
    assert '__BET_RESEARCH_FIXTURE__' in transport.read_text()
    transport.write_text(transport.read_text().replace('__BET_RESEARCH_FIXTURE__',fixture))
    app = Path('ios/KeibaAI/KeibaAI/KeibaAIApp.swift')
    body = app.read_text()
    assert body.count('struct KeibaAIApp: App {') == 1
    app.write_text(body.replace('struct KeibaAIApp: App {', 'struct KeibaAIApp: App {\n    init() { KeibaAIUITestTransport.enableIfRequested() }'))
    scheme = project / 'xcshareddata/xcschemes/KeibaAIPhoneUITests.xcscheme'
    scheme.parent.mkdir(parents=True, exist_ok=True)

    def ref(identifier, name):
        return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{identifier}" BuildableName="{name}" BlueprintName="{name.split(".")[0]}" ReferencedContainer="container:KeibaAI.xcodeproj"/>'

    scheme.write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2700" version="1.7">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries>
<BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="YES">{ref('21C9F935307B28F200A81BC9','KeibaAI.app')}</BuildActionEntry>
<BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="YES">{ref(ids['target'],'KeibaAIPhoneUITests.xctest')}</BuildActionEntry>
</BuildActionEntries></BuildAction>
<TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables><TestableReference skipped="NO">{ref(ids['target'],'KeibaAIPhoneUITests.xctest')}</TestableReference></Testables></TestAction>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{ref('21C9F935307B28F200A81BC9','KeibaAI.app')}</BuildableProductRunnable></LaunchAction>
</Scheme>''')


if __name__ == '__main__':
    prepare()
