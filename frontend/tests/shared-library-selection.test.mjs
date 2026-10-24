import test from 'node:test'
import assert from 'node:assert/strict'
import { chooseInitialLibrary, libraryPreferenceKey, readLibraryPreference, rememberLibraryPreference, libraryLabel } from '../src/utils/sharedLibraries.js'
const libraries = [{ id: 'a', name: '技术资料', team_name: '运维' }, { id: 'b', name: '项目资料', team_name: '工程' }]
function storage() { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) } }
test('one library enters automatically; first multiple libraries require a choice', () => { assert.equal(chooseInitialLibrary(libraries.slice(0, 1), null)?.id, 'a'); assert.equal(chooseInitialLibrary(libraries, null), null); assert.equal(chooseInitialLibrary([], 'a'), null) })
test('only a still accessible preference is restored', () => { assert.equal(chooseInitialLibrary(libraries, 'b')?.id, 'b'); assert.equal(chooseInitialLibrary(libraries, 'revoked'), null); assert.equal(chooseInitialLibrary(libraries.slice(0, 1), 'revoked')?.id, 'a') })
test('preferences never cross account or server and only store a library id', () => { const s = storage(); rememberLibraryPreference(s, 'server-a', 'alice', 'b'); assert.equal(readLibraryPreference(s, 'server-a', 'alice'), 'b'); assert.equal(readLibraryPreference(s, 'server-a', 'bob'), null); assert.equal(readLibraryPreference(s, 'server-b', 'alice'), null); assert.equal(s.getItem(libraryPreferenceKey('server-a', 'alice')), 'b'); assert.equal(libraryPreferenceKey('', 'alice'), null) })
test('storage errors do not prevent choosing a library', () => { const s = { getItem() { throw Error('disabled') }, setItem() { throw Error('full') } }; assert.equal(readLibraryPreference(s, 's', 'u'), null); assert.doesNotThrow(() => rememberLibraryPreference(s, 's', 'u', 'a')) })
test('library label distinguishes organizations', () => { assert.equal(libraryLabel(libraries[0]), '运维 / 技术资料'); assert.equal(libraryLabel({ name: '资料' }), '资料') })
